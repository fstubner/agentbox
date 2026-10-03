"""Device permissions travelling from the portal to the bridge.

The portal writes a file and a container reads it, and a fixture on either side
can pass while the seam between them is broken. So these write with the
portal's own writer and read with the bridge's own reader.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "services" / "templates" / "bridge" / "app"))
sys.path.insert(0, str(REPO / "services" / "templates" / "mcp"))
sys.path.insert(0, str(REPO / "services" / "compose" / "homeassistant-bridge" / "app"))


def load_household():
    spec = importlib.util.spec_from_file_location(
        "agentbox_household", REPO / "cli" / "agentbox_household.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["agentbox_household"] = module
    spec.loader.exec_module(module)
    return module


def load_bridge(policy_file: Path, env_controllable: str = ""):
    os.environ["HA_POLICY_FILE"] = str(policy_file)
    os.environ["HA_CONTROLLABLE_ENTITIES"] = env_controllable
    os.environ["HA_DENIED_ENTITIES"] = ""
    os.environ.pop("HA_CONTROLLABLE_DOMAINS", None)
    os.environ["HA_URL"] = "http://ha.test:8123"
    os.environ["HA_TOKEN"] = "t"
    spec = importlib.util.spec_from_file_location(
        f"ha_policy_bridge_{abs(hash(str(policy_file)))}",
        REPO / "services" / "compose" / "homeassistant-bridge" / "app" / "bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def policy(tmp_path):
    return load_household().HouseholdPolicy(directory=tmp_path)


# --- the seam ------------------------------------------------------------------


def test_what_the_portal_writes_is_what_the_bridge_reads(policy, tmp_path):
    """The whole point. Written by the portal's writer, read by the bridge's."""
    policy.save("media_player.tv, switch.washer")
    bridge = load_bridge(tmp_path / "household.json")
    assert bridge.controllable_entities() == {"media_player.tv", "switch.washer"}
    assert bridge.is_controllable("media_player.tv")
    assert not bridge.is_controllable("switch.boiler")


def test_the_file_wins_over_the_environment(policy, tmp_path):
    """Otherwise saving the form appears to work and changes nothing."""
    policy.save("switch.washer")
    bridge = load_bridge(tmp_path / "household.json",
                         env_controllable="switch.boiler")
    assert bridge.controllable_entities() == {"switch.washer"}
    assert not bridge.is_controllable("switch.boiler")


def test_a_saved_change_is_picked_up_without_a_restart(policy, tmp_path):
    """A settings page nobody can see the effect of is not a settings page."""
    policy.save("switch.washer")
    bridge = load_bridge(tmp_path / "household.json")
    assert bridge.is_controllable("switch.washer")
    policy.save("switch.washer, media_player.tv")
    assert bridge.is_controllable("media_player.tv")


def test_a_same_length_swap_is_not_missed(policy, tmp_path):
    """Two quick saves of the same length, as when correcting a typo, must not
    look unchanged, or a just-revoked permission would stay in force.
    """
    policy.save("switch.washer_a")
    bridge = load_bridge(tmp_path / "household.json")
    assert bridge.is_controllable("switch.washer_a")
    policy.save("switch.washer_b")
    assert not bridge.is_controllable("switch.washer_a")
    assert bridge.is_controllable("switch.washer_b")


def test_removing_an_entity_takes_effect_too(policy, tmp_path):
    """The direction that matters: revoking has to be as live as granting."""
    policy.save("switch.washer, media_player.tv")
    bridge = load_bridge(tmp_path / "household.json")
    assert bridge.is_controllable("media_player.tv")
    policy.save("switch.washer")
    assert not bridge.is_controllable("media_player.tv")


# --- what a bad file does ------------------------------------------------------


def test_a_missing_file_falls_back_rather_than_raising(tmp_path):
    """This is consulted on the refusal path; raising here breaks the bridge."""
    bridge = load_bridge(tmp_path / "absent.json", env_controllable="switch.washer")
    assert bridge.controllable_entities() == {"switch.washer"}


@pytest.mark.parametrize("content", [
    "not json at all",
    '{"controllable_entities": "switch.washer"}',    # a string, not a list
    '{"something_else": []}',
    "",
])
def test_a_malformed_file_never_widens_permission(tmp_path, content):
    """A broken file must narrow what is possible, never expand it."""
    path = tmp_path / "household.json"
    path.write_text(content, encoding="utf-8")
    bridge = load_bridge(path, env_controllable="switch.washer")
    assert bridge.controllable_entities() == {"switch.washer"}
    assert not bridge.is_controllable("lock.front_door")


# --- the second gate -----------------------------------------------------------


def test_the_bridge_refuses_security_domains_whatever_the_file_says(tmp_path):
    """The portal refuses these when typed, but that only guards against typos.
    The refusal that holds under compromise is in the process holding the
    credential, and it ignores this list.
    """
    path = tmp_path / "household.json"
    path.write_text(json.dumps({"controllable_entities": [
        "lock.front_door", "alarm_control_panel.house", "cover.garage",
        "camera.hall", "vacuum.robot"]}), encoding="utf-8")
    bridge = load_bridge(path)
    # The file is read, so these are refused by the bridge, not merely absent.
    assert "lock.front_door" in bridge.controllable_entities()
    for entity in ("lock.front_door", "alarm_control_panel.house",
                   "cover.garage", "camera.hall", "vacuum.robot"):
        assert not bridge.is_controllable(entity), entity


def test_the_portal_and_the_bridge_agree_on_what_is_refused(tmp_path):
    """The portal must not accept a domain the bridge refuses, or someone would
    grant something that silently does nothing. The reverse is only cautious,
    so only one direction is checked.
    """
    household = load_household()
    bridge = load_bridge(tmp_path / "absent.json")
    assert set(household.NEVER_CONTROLLABLE) <= set(bridge.SECURITY_DOMAINS)
