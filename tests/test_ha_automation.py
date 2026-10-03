"""Tests for Home Assistant automations the assistant writes.

An automation is stored code that Home Assistant runs later with its own
privileges. The bridge refuses things at call time, and an automation is not a
call, so without validation an assistant that may not unlock a door could
write an automation that unlocks it at 3am.

Most of these try to reach a lock. The most important is the template test.
Templates are refused outright, because `service: "{{ ... }}"` decides at
runtime and would make every other check a guess.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from conftest import strip_comments

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
    # The motion sensor is read, not acted on, so the assistant not being able
    # to control it must not make the automation invalid.
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
    """The operator reads this when approving, so it must say accurately which
    entities are acted on and which are only read."""
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
    """Refused domains are matched as domains, so any service in them, such as
    `rest_command.post`, is refused."""
    body = {"trigger": {}, "action": {"service": service,
                                      "entity_id": "light.hall"}}
    with pytest.raises(auto.AutomationRefused):
        check("alias: x", body)


def test_the_apparmor_profile_permits_bluetooth_device_discovery():
    """BlueZ announces a new device with InterfacesAdded on the root path, from
    its unique connection name, not under /org/bluez or from org.bluez. The
    AppArmor profile must allow that, or Bluetooth sensors never appear in
    Home Assistant.
    """
    from pathlib import Path
    profile = (Path(__file__).resolve().parents[1]
               / "services/apparmor/agentbox-homeassistant").read_text()
    rules = strip_comments(profile)
    assert "path=/ interface=org.freedesktop.DBus.ObjectManager" in \
        rules.replace("\n", " ").replace("       ", " ").replace("  ", " ")


def test_the_discovery_rule_is_scoped_to_one_interface():
    """A bare `dbus receive bus=system path=/` would permit receiving signals
    from anything on the bus, which is a much wider grant than the problem
    needed."""
    from pathlib import Path
    profile = (Path(__file__).resolve().parents[1]
               / "services/apparmor/agentbox-homeassistant").read_text()
    block = profile.split("Device discovery")[1].split("dbus receive")[1][:200]
    assert "interface=org.freedesktop.DBus.ObjectManager" in block
