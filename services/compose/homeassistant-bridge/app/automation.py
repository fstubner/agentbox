"""Validation for assistant-authored Home Assistant automations.

An automation is not a tool call. It is **stored code that Home Assistant
executes later, with Home Assistant's privileges** — which are total. Every
refusal in bridge.py happens at call time and an automation is not a call, so
without this module an assistant that may not unlock a door may write:

    trigger: {platform: time, at: "03:00:00"}
    action:  {service: lock.unlock, entity_id: lock.front_door}

and the door opens at three in the morning. This is the `merge_own_pr` problem
in a different costume: producing code that later runs with more authority than
the producer has.

Home Assistant's config API takes JSON, so nothing here parses YAML — the
automation arrives as a JSON object and the template scan runs on its
serialisation.

## Why this refuses templates

Home Assistant automations are a templating language. `service: "{{
states('input_text.x') }}"` is legal, and so are computed entity ids, `repeat`,
`choose`, and scripts calling scripts. Deciding whether such an automation ever
reaches `lock.unlock` is program analysis over a Turing-complete template
engine — any denylist has holes, and a denylist with holes is worse than none
because it reads as protection.

So assistant-authored automations are **template-free**. No `{{ }}`, no `{% %}`.
Every service name and entity id is then a literal, static checking becomes
decidable, and this module can actually make the guarantee it claims.

The cost is real: the assistant cannot write a clever automation. It can write
"when the hall motion sensor triggers after sunset, turn on the hall light",
which is what people actually want from one. An operator who needs a templated
automation writes it themselves, which they were doing anyway.

## What this does not do

It does not decide whether the automation is a *good idea* — only that it
cannot reach anything the assistant is not allowed to touch directly. Intent is
what the operator approves; safety is what this enforces. The two are separate
on purpose, because an approval prompt is a bad place to be discovering that
something was never permitted in the first place.
"""
from __future__ import annotations

import re
from typing import Any

# Anything that defers a decision to runtime. Checked on the raw YAML text
# before parsing, so an encoding trick cannot smuggle one past the walk below.
TEMPLATE_MARKERS = ("{{", "}}", "{%", "%}")

# Keys whose values name something to act on. Collected wherever they appear at
# any depth, because `choose`, `repeat`, `parallel` and `if/then` all nest
# actions arbitrarily and a fixed-shape check would miss them.
SERVICE_KEYS = ("service", "action")
ENTITY_KEYS = ("entity_id", "device_id", "target")

# Service domains that can reach the physical world in ways this platform never
# permits. Kept in step with bridge.SECURITY_DOMAINS; the duplication is
# deliberate — this must fail closed on its own rather than depend on an import
# that a future refactor could quietly change the meaning of.
FORBIDDEN_DOMAINS = frozenset({
    "lock", "alarm_control_panel", "cover", "garage_door", "camera", "vacuum",
})

# Whole service *domains* that reach arbitrary code, arbitrary hosts, or the
# supervisor. Matched on the domain rather than on individual service names —
# an earlier version listed `"rest_command"` among full names like
# `"homeassistant.turn_on"` and compared both against `service`, so
# `rest_command.post` matched neither and was allowed. Every service in these
# domains is out, whatever it is called.
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
                    # `target: {entity_id: [...]}` — recurse rather than
                    # stringify, or a nested target slips through unread.
                    _walk(value, found_services, found_entities)
                    continue
            _walk(value, found_services, found_entities)
    elif isinstance(node, list):
        for item in node:
            _walk(item, found_services, found_entities)


def validate(raw_text: str, parsed: Any, controllable: frozenset[str]) -> dict:
    """Refuse an automation that could reach anything it must not.

    `raw_text` is the serialised automation as sent by the caller — the
    template scan runs on that rather than on the parsed tree, so a template
    hiding in a key, a comment or an unusual encoding still trips it.

    Returns a summary of what it will touch, for the operator to read when
    approving. Raises AutomationRefused otherwise.
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
                f"covers are never actuated by this platform — not directly "
                f"and not by an automation it wrote, which would be the same "
                f"thing on a delay.")
        if domain in FORBIDDEN_SERVICE_DOMAINS or service.lower() in FORBIDDEN_SERVICES:
            raise AutomationRefused(
                f"'{service}' can reach services, hosts or code outside what "
                f"the assistant may call directly, so an automation using it "
                f"would be an escalation.")

    # Every entity must be one the assistant could already act on. An
    # automation must not be a way to touch something a direct call would
    # refuse.
    for entity in entities:
        if not entity or "." not in entity:
            raise AutomationRefused(f"not an entity id: {entity!r}")
        domain = entity.split(".", 1)[0].lower()
        if domain in FORBIDDEN_DOMAINS:
            raise AutomationRefused(
                f"'{entity}' is in the {domain} domain and is never actuated.")
        # Triggers legitimately reference sensors the assistant cannot control
        # — "when the hall motion sensor fires" is the normal case — so only
        # entities in *action* position need to be controllable. Distinguishing
        # them reliably means checking the action subtree specifically.
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
                f"HA_CONTROLLABLE_ENTITIES first if that is intended.")

    return {
        "services": sorted(set(services)),
        "acts_on": sorted(set(action_entities)),
        "reads": sorted(set(entities) - set(action_entities)),
    }
