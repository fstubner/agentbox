"""The settings page: who may write it, and what a rejection does.

The store's own validation is covered in test_settings_store.py. What matters
here is the seam between the form and the store — the place where a value
stops being text somebody typed and starts deciding who is an admin.
"""
from __future__ import annotations

import pytest
from test_portal_auth import load_portal


@pytest.fixture
def portal(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOX_POLICY_DIR", str(tmp_path / "policy"))
    return load_portal(tmp_path, monkeypatch)


# --- who may change settings ---------------------------------------------------


def test_a_member_cannot_write_settings(portal):
    """`admins` decides privilege, so writing it is not a member's to do."""
    assert not portal.can(portal.MEMBER, "ops:write_settings")
    assert not portal.can(portal.MEMBER, "ops:write_household")
    assert portal.can(portal.ADMIN, "ops:write_settings")


def test_an_agent_minted_link_cannot_change_settings(portal):
    """The escalation this exists to stop.

    If a link the assistant produced could edit `admins`, the assistant could
    make any identity an admin; if it could edit `identity_emails`, it could
    point somebody's sign-in link at a mailbox it reads. Admin role is not
    enough — the origin has to be one a person actually asked for.
    """
    for origin in (portal.ORIGIN_AGENT, portal.ORIGIN_CHAT):
        assert not portal.can(portal.ADMIN, "ops:write_settings", origin)
        assert not portal.can(portal.ADMIN, "ops:write_household", origin)
    assert portal.can(portal.ADMIN, "ops:write_settings", portal.ORIGIN_EMAIL)


def test_settings_writes_are_in_the_withheld_set(portal):
    """Stated once here so removing it from AGENT_WITHHELD fails a test.

    The capability check above would still pass if someone granted these to
    agent links deliberately; this pins the intent.
    """
    assert "ops:write_settings" in portal.AGENT_WITHHELD
    assert "ops:write_household" in portal.AGENT_WITHHELD


# --- what the form does with what it is given ----------------------------------


def test_one_bad_field_changes_nothing(portal):
    """A half-applied settings page is how you get an admin list nobody chose."""
    portal.SETTINGS.save({"admins": "alex", "smtp_host": "smtp.example.com"})
    with pytest.raises(portal.agentbox_settings.InvalidSetting):
        portal.SETTINGS.save({"admins": "sam", "smtp_port": "not-a-port"})
    assert portal.SETTINGS.value("admins") == "alex"
    assert portal.SETTINGS.value("smtp_host") == "smtp.example.com"


def test_a_rejection_says_which_field_was_wrong(portal):
    """So the page can put the message against the input, not at the top."""
    with pytest.raises(portal.agentbox_settings.InvalidSetting) as caught:
        portal.SETTINGS.save({"smtp_port": "99999"})
    assert caught.value.key == "smtp_port"


def test_the_secret_is_never_rendered_into_the_page(portal):
    """It would otherwise sit in every admin's history and page cache."""
    portal.SETTINGS.save({"smtp_password": "hunter2-app-password"})
    body = portal.render_admin("alex", "").decode()
    assert "hunter2-app-password" not in body
    # But the page still says one is stored, or nobody can tell it apart from
    # a box with no password at all.
    assert "stored" in body


def test_a_blank_secret_keeps_the_stored_one(portal):
    """The form submits every field, so blank has to mean "unchanged"."""
    portal.SETTINGS.save({"smtp_password": "kept"})
    portal.SETTINGS.save({"smtp_password": "", "smtp_host": "smtp.example.com"})
    assert portal.SETTINGS.value("smtp_password") == "kept"


def test_every_setting_appears_on_the_page(portal):
    """The renderer is table-driven; this fails if it stops being.

    A setting that exists in the table but not on the page is worse than one
    that is missing entirely — it looks configurable and is not.
    """
    body = portal.render_admin("alex", "").decode()
    for setting in portal.agentbox_settings.SETTINGS:
        assert f"name='{setting.key}'" in body, setting.key


def test_a_validation_message_never_travels_through_the_flash_table(portal):
    """Flash keys are a fixed table so no one can craft a link that shows
    another admin an arbitrary sentence. Validation messages quote what was
    typed, so they must be rendered from the POST instead of redirected.
    """
    import inspect
    source = inspect.getsource(portal.PortalHandler._save_settings)
    assert "self._send(400" in source
    assert "self._redirect" in source     # only on success
    # The error text must not be reachable from flash_text, which would mean
    # it had been round-tripped through the URL.
    assert portal.flash_text("'99999' is not a port number") == ""


# --- device permissions --------------------------------------------------------


def test_a_refused_domain_is_rejected_rather_than_silently_ignored(portal):
    """Storing it would leave somebody believing they granted something."""
    with pytest.raises(portal.agentbox_household.InvalidEntity):
        portal.HOUSEHOLD.save("lock.front_door")
    assert portal.HOUSEHOLD.controllable() == []


def test_household_policy_is_not_in_the_portal_state_directory(portal, tmp_path):
    """It is read by a container, so it cannot sit beside the admin list.

    Mounting the portal's directory into the Home Assistant bridge to reach
    this file would hand the assistant a route to every setting in it.
    """
    assert portal.SETTINGS.path.parent != portal.HOUSEHOLD.path.parent
    assert tmp_path in portal.SETTINGS.path.parents
    portal.HOUSEHOLD.save("media_player.tv")
    assert portal.HOUSEHOLD.path.read_text()
