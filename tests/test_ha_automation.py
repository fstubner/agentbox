"""Tests for assistant-authored Home Assistant automations.

An automation is stored code that Home Assistant runs later with its own
privileges. Every refusal in the bridge happens at call time, and an automation
is not a call — so without validation, an assistant forbidden from unlocking a
door can simply write an automation that unlocks it at 3am. Same escalation as
merging your own PR, on a timer.

Most of these are attempts to reach a lock. The one that matters most is the
template test: templates are refused wholesale, because `service: "{{ ... }}"`
defers the decision to runtime and makes every other check here a guess.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "services" / "compose" / "homeassistant-bridge" / "app"


def load():
    spec = importlib.util.spec_from_file_location("ha_automation", APP / "automation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


auto = load()
CONTROLLABLE = frozenset({"light.hall", "light.kitchen", "scene.evening"})


def check(yaml_text: str, parsed, controllable=CONTROLLABLE):
    return auto.validate(yaml_text, parsed, controllable)


GOOD = {
    "alias": "Hall light on motion after sunset",
    "trigger": {"platform": "state", "entity_id": "binary_sensor.hall_motion",
                "to": "on"},
    "condition": {"condition": "sun", "after": "sunset"},
    "action": {"service": "light.turn_on", "entity_id": "light.hall"},
}


def test_an_ordinary_automation_passes():
    summary = check("alias: x", GOOD)
    assert summary["services"] == ["light.turn_on"]
    assert summary["acts_on"] == ["light.hall"]
    # The motion sensor is read, not acted on — and the assistant cannot
    # control it, which must not make the automation illegal.
    assert "binary_sensor.hall_motion" in summary["reads"]


# --- templates ----------------------------------------------------------------


@pytest.mark.parametrize("text", [
    'action: {service: "{{ states(\'input_text.x\') }}"}',
    "action: {service: light.turn_on}\ncondition: '{% if x %}'",
    "alias: '{{ 1 + 1 }}'",
])
def test_templates_are_refused(text):
    """The single decision that makes every other check here sound. A templated
    automation computes its service at runtime, so no static check can say what
    it will do."""
    with pytest.raises(auto.AutomationRefused) as exc:
        check(text, {"trigger": {}, "action": {"service": "light.turn_on",
                                               "entity_id": "light.hall"}})
    assert "template" in str(exc.value).lower()


def test_the_template_check_reads_the_raw_text_not_the_parsed_tree():
    """Checked before parsing, so a template hidden in a key, a comment or an
    unusual encoding still trips it."""
    with pytest.raises(auto.AutomationRefused):
        check("# {{ sneaky }}", GOOD)


# --- reaching a lock ------------------------------------------------------------


LOCK_ATTEMPTS = [
    ("direct service call",
     {"trigger": {"platform": "time", "at": "03:00:00"},
      "action": {"service": "lock.unlock", "entity_id": "lock.front_door"}}),
    ("nested in choose",
     {"trigger": {}, "action": {"choose": [{"conditions": [], "sequence": [
         {"service": "lock.unlock", "entity_id": "lock.front_door"}]}]}}),
    ("nested in repeat",
     {"trigger": {}, "action": {"repeat": {"count": 1, "sequence": [
         {"service": "lock.open", "entity_id": "lock.back"}]}}}),
    ("inside parallel",
     {"trigger": {}, "action": {"parallel": [
         {"service": "light.turn_on", "entity_id": "light.hall"},
         {"service": "lock.unlock", "entity_id": "lock.front_door"}]}}),
    ("alarm panel",
     {"trigger": {}, "action": {"service": "alarm_control_panel.alarm_disarm",
                                "entity_id": "alarm_control_panel.house"}}),
    ("garage via cover",
     {"trigger": {}, "action": {"service": "cover.open_cover",
                                "entity_id": "cover.garage"}}),
]


@pytest.mark.parametrize("label,body", LOCK_ATTEMPTS, ids=[a[0] for a in LOCK_ATTEMPTS])
def test_an_automation_cannot_reach_a_security_domain(label, body):
    with pytest.raises(auto.AutomationRefused) as exc:
        check("alias: x", body)
    message = str(exc.value).lower()
    assert "never actuated" in message or "domain" in message


def test_nesting_depth_does_not_hide_a_lock():
    """`choose` inside `repeat` inside `if` is legal HA and a fixed-shape check
    would walk right past it."""
    body = {"trigger": {}, "action": {
        "if": [{"condition": "state"}],
        "then": [{"repeat": {"count": 2, "sequence": [
            {"choose": [{"conditions": [], "sequence": [
                {"service": "lock.unlock", "entity_id": "lock.front_door"}]}]}]}}]}}
    with pytest.raises(auto.AutomationRefused):
        check("alias: x", body)


def test_a_lock_inside_a_target_block_is_found():
    """`target: {entity_id: ...}` is the modern form and must not be treated as
    an opaque value."""
    body = {"trigger": {}, "action": {"service": "lock.unlock",
                                      "target": {"entity_id": ["lock.front"]}}}
    with pytest.raises(auto.AutomationRefused):
        check("alias: x", body)


# --- escalation by generic service ------------------------------------------------


@pytest.mark.parametrize("service", [
    "homeassistant.turn_on",     # acts on any domain, including lock
    "shell_command.anything",
    "python_script.exec",
    "rest_command.post",
    "automation.reload",
])
def test_services_that_reach_further_are_refused(service):
    body = {"trigger": {}, "action": {"service": service,
                                      "entity_id": "light.hall"}}
    with pytest.raises(auto.AutomationRefused) as exc:
        check("alias: x", body)
    assert "escalation" in str(exc.value).lower()


# --- the allowlist ----------------------------------------------------------------


def test_an_automation_cannot_act_on_a_non_allowlisted_entity():
    """An automation must not become a route to something a direct call would
    refuse."""
    body = {"trigger": {}, "action": {"service": "light.turn_on",
                                      "entity_id": "light.bedroom"}}
    with pytest.raises(auto.AutomationRefused) as exc:
        check("alias: x", body)
    assert "controllable list" in str(exc.value)


def test_triggers_may_reference_uncontrollable_entities():
    """"When the motion sensor fires" is the normal case, and the assistant can
    never control a motion sensor. Refusing this would make the feature
    useless."""
    summary = check("alias: x", GOOD)
    assert "binary_sensor.hall_motion" in summary["reads"]


# --- shape --------------------------------------------------------------------------


@pytest.mark.parametrize("body", [
    {"action": {"service": "light.turn_on", "entity_id": "light.hall"}},  # no trigger
    {"trigger": {"platform": "time"}},                                    # no action
    "not a mapping",
    ["also", "not"],
])
def test_malformed_automations_are_refused(body):
    with pytest.raises(auto.AutomationRefused):
        check("alias: x", body)


def test_an_automation_that_does_nothing_is_refused():
    body = {"trigger": {"platform": "time", "at": "03:00"}, "action": []}
    with pytest.raises(auto.AutomationRefused) as exc:
        check("alias: x", body)
    assert "no service" in str(exc.value)


def test_the_summary_separates_what_it_reads_from_what_it_changes():
    """This is what the operator reads when approving, so the distinction has
    to be accurate — 'acts on your hall light' is a different decision from
    'reads your motion sensor'."""
    summary = check("alias: x", GOOD)
    assert summary["acts_on"] == ["light.hall"]
    assert "light.hall" not in summary["reads"]


def test_forbidden_domains_match_the_bridge():
    """Duplicated deliberately so this module fails closed on its own, but the
    two must not drift apart."""
    import os
    os.environ.setdefault("HA_CONTROLLABLE_ENTITIES", "")
    spec = importlib.util.spec_from_file_location("ha_bridge_for_domains",
                                                  APP / "bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(REPO / "services" / "templates" / "bridge" / "app"))
    sys.path.insert(0, str(APP))
    spec.loader.exec_module(bridge)
    assert bridge.SECURITY_DOMAINS <= auto.FORBIDDEN_DOMAINS


@pytest.mark.parametrize("service", [
    "hassio.host_reboot",
    "hassio.addon_start",
    "supervisor.restart",
    "rest_command.exfiltrate",
    "automation.turn_off",
    "script.reload",
])
def test_supervisor_and_reload_services_are_refused(service):
    """Added after `rest_command.post` slipped through: the first version
    listed bare domains alongside full service names and compared both against
    the full name, so a domain entry matched nothing. Domains are matched as
    domains now."""
    body = {"trigger": {}, "action": {"service": service,
                                      "entity_id": "light.hall"}}
    with pytest.raises(auto.AutomationRefused):
        check("alias: x", body)
