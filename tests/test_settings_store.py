"""Household settings, and the rules that keep them from becoming a hazard.

Two of them decide privilege, who is an admin and where a sign-in link goes, so
the interesting tests are about submitting nonsense and whether the assistant
can reach them.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _module(name: str, filename: str):
    loader = importlib.machinery.SourceFileLoader(name, str(REPO / "cli" / filename))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


settings_mod = _module("agentbox_settings_test", "agentbox_settings.py")
household_mod = _module("agentbox_household_test", "agentbox_household.py")


@pytest.fixture
def store(tmp_path):
    return settings_mod.SettingsStore(directory=tmp_path / "portal", environ={})


# --- precedence ----------------------------------------------------------------


def test_a_stored_value_wins_over_the_environment(tmp_path):
    store = settings_mod.SettingsStore(
        directory=tmp_path, environ={"AGENTBOX_ADMINS": "old"})
    assert store.value("admins") == "old"
    store.save({"admins": "alex"})
    assert store.value("admins") == "alex"


def test_a_box_configured_the_old_way_keeps_working(tmp_path):
    """Nobody should have to migrate to keep a working household."""
    store = settings_mod.SettingsStore(
        directory=tmp_path, environ={"AGENTBOX_ADMINS": "alex"})
    assert store.value("admins") == "alex"


def test_an_unset_setting_falls_back_to_its_default(store):
    assert store.value("smtp_port") == "587"
    assert store.value("admins") == ""


# --- validation ----------------------------------------------------------------


def test_a_bad_entry_changes_nothing_at_all(store):
    """A half-applied settings page is how somebody ends up with an admin list
    they did not intend."""
    store.save({"admins": "alex"})
    with pytest.raises(settings_mod.InvalidSetting):
        store.save({"admins": "sam", "identity_emails": "not-an-email"})
    assert store.value("admins") == "alex"
    assert store.value("identity_emails") == ""


def test_names_must_look_like_identities(store):
    with pytest.raises(settings_mod.InvalidSetting) as exc:
        store.save({"admins": "Alex Carter"})
    assert "not an identity name" in str(exc.value)


def test_a_pair_without_a_colon_is_explained_not_dropped(store):
    """A list that quietly loses an entry leaves somebody believing they
    configured something they did not."""
    with pytest.raises(settings_mod.InvalidSetting) as exc:
        store.save({"identity_emails": "alex@example.com"})
    assert "name:value" in str(exc.value)


def test_ports_are_checked(store):
    for bad in ("0", "70000", "eight"):
        with pytest.raises(settings_mod.InvalidSetting):
            store.save({"smtp_port": bad})


def test_blank_is_allowed_where_blank_means_off(store):
    store.save({"smtp_host": "", "identity_emails": ""})
    assert store.value("smtp_host") == ""


# --- secrets -------------------------------------------------------------------


def test_a_blank_password_keeps_the_existing_one(store):
    """The form cannot echo a password back, so submitting the page must not
    wipe it."""
    store.save({"smtp_password": "app-password"})
    store.save({"smtp_host": "smtp.example.com", "smtp_password": ""})
    assert store.value("smtp_password") == "app-password"
    assert store.value("smtp_host") == "smtp.example.com"


def test_the_file_is_not_world_readable(store):
    store.save({"smtp_password": "app-password"})
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_a_crash_mid_write_cannot_empty_the_admin_list(store):
    """Written whole then moved: a truncated file that reads as 'nobody is an
    admin' would lock everyone out of Operations."""
    store.save({"admins": "alex"})
    assert json.loads(store.path.read_text())["admins"] == "alex"
    # The temp file is never the live path.
    assert not store.path.with_suffix(".tmp").exists()


def test_saving_reports_only_what_changed(store):
    assert store.save({"admins": "alex"}) == ["admins"]
    assert store.save({"admins": "alex"}) == []


# --- the household policy, which a container reads -----------------------------


def test_device_permissions_are_not_in_the_portal_only_file():
    """The settings file is safe because no container mounts it. Device
    permissions must be readable by the Home Assistant bridge, so they live
    elsewhere rather than giving the assistant a route to the admin list."""
    # Named explicitly rather than pattern-matched: "identity_emails"
    # contains the substring "entit", and a test that passes by accident is
    # worse than no test.
    device_keys = {"controllable", "controllable_entities", "entities",
                   "devices", "ha_controllable_entities"}
    assert not device_keys & set(settings_mod.BY_KEY)
    # And the household policy is the thing that does hold them.
    assert hasattr(household_mod.HouseholdPolicy, "controllable")


def test_entity_ids_are_validated(tmp_path):
    policy = household_mod.HouseholdPolicy(directory=tmp_path)
    with pytest.raises(household_mod.InvalidEntity) as exc:
        policy.save("kitchen light")
    assert "not an entity id" in str(exc.value)


def test_a_domain_the_bridge_refuses_is_rejected_rather_than_ignored(tmp_path):
    """Accepting it would show somebody a saved setting that does nothing."""
    policy = household_mod.HouseholdPolicy(directory=tmp_path)
    for entity in ("lock.front_door", "alarm_control_panel.house",
                   "cover.garage", "camera.hallway"):
        with pytest.raises(household_mod.InvalidEntity) as exc:
            policy.save(entity)
        assert "refuses in code" in str(exc.value)


def test_valid_entities_are_stored_deduplicated_and_sorted(tmp_path):
    policy = household_mod.HouseholdPolicy(directory=tmp_path)
    saved = policy.save("switch.washer, light.hall\nswitch.washer")
    assert saved == ["light.hall", "switch.washer"]
    assert policy.controllable() == ["light.hall", "switch.washer"]


def test_one_bad_entity_rejects_the_whole_list(tmp_path):
    policy = household_mod.HouseholdPolicy(directory=tmp_path)
    policy.save("switch.washer")
    with pytest.raises(household_mod.InvalidEntity):
        policy.save("switch.washer, lock.front_door")
    assert policy.controllable() == ["switch.washer"]


def test_the_policy_file_is_readable_by_the_container_that_mounts_it(tmp_path):
    policy = household_mod.HouseholdPolicy(directory=tmp_path)
    policy.save("switch.washer")
    assert policy.path.stat().st_mode & 0o777 == 0o644
