"""Validation for Home Assistant automations the assistant writes.

An automation is stored code that Home Assistant runs later with its own
privileges, which are unrestricted. Every refusal in bridge.py
happens at call time, so without this module an assistant that may not unlock a
door could write

    trigger: {platform: time, at: "03:00:00"}
    action:  {service: lock.unlock, entity_id: lock.front_door}

and the door would open at three in the morning. This is the same problem as
an assistant merging its own change. In both cases code runs later with more
authority than its author has.

The automation arrives as JSON, and the template scan runs on its text.

## Why templates are refused

Home Assistant automations can be templated. Services and entity ids can be
computed, and `repeat`, `choose` and scripts calling scripts are all allowed.
Deciding whether such an automation ever reaches `lock.unlock` would mean
analysing a Turing-complete template engine, and any denylist would have holes.

So automations written by the assistant contain no templates, no `{{ }}` and no
`{% %}`. Every service and entity id is then a literal, and checking them is
decidable. The assistant cannot write complex automations. It can still write
simple ones such as "when the hall motion sensor fires after sunset, turn on
the hall light". Templated automations are for the owner to write.

## What this does not decide

This module does not decide whether an automation is a good idea. It proves the automation cannot
reach anything the assistant could not touch directly. The operator approves
the intent.
"""
from __future__ import annotations

from typing import Any

# Anything that defers a decision to runtime. Checked on the raw text before
# parsing, so an encoding trick cannot hide one from the walk below.
TEMPLATE_MARKERS = ("{{", "}}", "{%", "%}")

# Keys whose values name something to act on. Collected at any depth, because
# `choose`, `repeat`, `parallel` and `if/then` nest actions arbitrarily.
SERVICE_KEYS = ("service", "action")
ENTITY_KEYS = ("entity_id", "device_id", "target")

# Service domains that reach the physical world in ways never permitted here.
# The same list as bridge.SECURITY_DOMAINS, repeated here so this check fails
# closed on its own instead of depending on an import.
FORBIDDEN_DOMAINS = frozenset({
    "lock", "alarm_control_panel", "cover", "garage_door", "camera", "vacuum",
})

# Whole service domains that reach arbitrary code, arbitrary hosts or the
# supervisor. Matched on the domain, so every service in them is refused
# whatever it is called.
FORBIDDEN_SERVICE_DOMAINS = frozenset({
    "shell_command",    # arbitrary host commands
    "python_script",    # arbitrary code
    "rest_command",     # arbitrary outbound HTTP, i.e. exfiltration
    "hassio",           # supervisor: add-ons, host reboot
    "supervisor",
})

# Individual services that reach further than their domain suggests.
FORBIDDEN_SERVICES = frozenset({
    "homeassistant.turn_on",     # generic: acts on any domain, including lock
    "homeassistant.turn_off",
    "homeassistant.toggle",
    "automation.reload",         # could load an automation written elsewhere
    "automation.turn_off",       # could disable a safety automation
    "script.reload",
})


class AutomationRefused(Exception):
    """Raised when an automation could reach something it must not."""


def _walk(node: Any, found_services: list, found_entities: list) -> None:
    """Collect every service and entity reference at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            lowered = str(key).lower()
            if lowered in SERVICE_KEYS and isinstance(value, str):
                found_services.append(value.strip())
            elif lowered in ENTITY_KEYS:
                if isinstance(value, str):
                    found_entities.append(value.strip())
                elif isinstance(value, list):
                    found_entities.extend(str(v).strip() for v in value)
                elif isinstance(value, dict):
                    # `target: {entity_id: [...]}`. Recurse, so a nested
                    # target is read.
                    _walk(value, found_services, found_entities)
                    continue
            _walk(value, found_services, found_entities)
    elif isinstance(node, list):
        for item in node:
            _walk(item, found_services, found_entities)


def validate(raw_text: str, parsed: Any, controllable: frozenset[str]) -> dict:
    """Refuse an automation that could reach anything it must not.

    The template scan runs on `raw_text`, the automation as the caller sent it,
    so a template hidden in a key or an unusual encoding still trips it.

    Returns a summary of what it will touch, for the operator to read when
    approving, or raises AutomationRefused.
    """
    for marker in TEMPLATE_MARKERS:
        if marker in raw_text:
            raise AutomationRefused(
                f"templates are not allowed in assistant-authored automations "
                f"(found {marker!r}). A templated automation can compute the "
                f"service it calls at runtime, which makes it impossible to "
                f"check what it will do. Write it with literal entity ids and "
                f"service names, or ask the operator to write it by hand.")

    if not isinstance(parsed, dict):
        raise AutomationRefused("an automation must be a YAML mapping")
    for required in ("trigger", "action"):
        if required not in parsed:
            raise AutomationRefused(f"automation is missing '{required}'")

    services: list[str] = []
    entities: list[str] = []
    _walk(parsed, services, entities)

    if not services:
        raise AutomationRefused("automation calls no service; it would do nothing")

    for service in services:
        if not service or "." not in service:
            raise AutomationRefused(f"not a service name: {service!r}")
        domain = service.split(".", 1)[0].lower()
        if domain in FORBIDDEN_DOMAINS:
            raise AutomationRefused(
                f"'{service}' is in the {domain} domain. Locks, alarms and "
                f"covers are never actuated by this platform, not directly "
                f"and not by an automation it wrote, which would be the same "
                f"thing on a delay.")
        if domain in FORBIDDEN_SERVICE_DOMAINS or service.lower() in FORBIDDEN_SERVICES:
            raise AutomationRefused(
                f"'{service}' can reach services, hosts or code outside what "
                f"the assistant may call directly, so an automation using it "
                f"would be an escalation.")

    # Every entity must be one the assistant could already act on directly.
    for entity in entities:
        if not entity or "." not in entity:
            raise AutomationRefused(f"not an entity id: {entity!r}")
        domain = entity.split(".", 1)[0].lower()
        if domain in FORBIDDEN_DOMAINS:
            raise AutomationRefused(
                f"'{entity}' is in the {domain} domain and is never actuated.")
        # Triggers may name sensors the assistant cannot control, such as a
        # motion sensor, so only entities in the actions must be controllable.
    action_services: list[str] = []
    action_entities: list[str] = []
    _walk(parsed.get("action"), action_services, action_entities)
    for entity in action_entities:
        if "." not in entity:
            continue
        if entity not in controllable:
            raise AutomationRefused(
                f"'{entity}' is not in the operator's controllable list, so an "
                f"automation must not act on it either. Add it to "
                f"it on the portal's Operations page first if that is intended.")

    return {
        "services": sorted(set(services)),
        "acts_on": sorted(set(action_entities)),
        "reads": sorted(set(entities) - set(action_entities)),
    }
