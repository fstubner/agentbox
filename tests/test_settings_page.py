"""The settings page: who may write it, and what a rejection does.

The store's own validation is covered in test_settings_store.py. What matters
here is the seam between the form and the store — the place where a value
stops being text somebody typed and starts deciding who is an admin.
"""
from __future__ import annotations

import sys

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


def test_a_member_is_not_told_their_link_is_the_problem(portal):
    """Found by testing the live box, not by a unit test.

    `can` collapses two gates into one boolean, and all three admin-only
    handlers assumed a refusal meant the origin. A member was told to sign in
    from a different link — which produces exactly the same refusal, because
    the link was never the issue.
    """
    assert portal.refusal(portal.MEMBER, "ops:write_settings",
                          portal.ORIGIN_OPERATOR) == "admin_only"
    assert portal.refusal(portal.MEMBER, "ops:invite",
                          portal.ORIGIN_EMAIL) == "admin_only"
    # The origin message stays for the case it is actually true of.
    assert portal.refusal(portal.ADMIN, "ops:write_settings",
                          portal.ORIGIN_AGENT) == "agent_link_cannot_configure"
    assert portal.refusal(portal.ADMIN, "ops:invite",
                          portal.ORIGIN_CHAT) == "agent_link_cannot_configure"
    # And nothing is refused when nothing should be.
    assert portal.refusal(portal.ADMIN, "ops:invite", portal.ORIGIN_EMAIL) == ""


def test_every_refusal_reason_has_a_message(portal):
    """A key with no entry renders nothing, so a wrong key is a silent page."""
    for role in (portal.MEMBER, portal.ADMIN):
        for origin in (portal.ORIGIN_EMAIL, portal.ORIGIN_OPERATOR,
                       portal.ORIGIN_AGENT, portal.ORIGIN_CHAT):
            for cap in ("ops:write_settings", "ops:write_household", "ops:invite"):
                key = portal.refusal(role, cap, origin)
                if key:
                    assert portal.flash_text(key), key


def test_connecting_a_chat_account_is_withheld_from_agent_links(portal):
    """Found by enabling /agent/link and driving it, not by reading the code.

    Unlinking was withheld because "moving where somebody's sign-in links
    arrive is not a convenience". Pairing was not — and pairing is the same
    act in the more dangerous direction: unlinking removes a channel, pairing
    points one somewhere new. The code it mints is shown on a page any
    agent-minted session can read, so an attacker who can inject into the
    assistant and control any non-bot Discord account could have had that
    person's sign-in links delivered to them.
    """
    for origin in (portal.ORIGIN_AGENT, portal.ORIGIN_CHAT):
        assert not portal.can(portal.ADMIN, "connector:pair_chat", origin)
        assert not portal.can(portal.MEMBER, "connector:pair_chat", origin)
    # The ordinary path still works, or nobody could ever connect Discord.
    for origin in (portal.ORIGIN_EMAIL, portal.ORIGIN_OPERATOR):
        assert portal.can(portal.MEMBER, "connector:pair_chat", origin)
    assert "connector:pair_chat" in portal.AGENT_WITHHELD


def test_pairing_and_unlinking_are_withheld_together(portal):
    """They are the same decision. Withholding one and not the other is how
    this was wrong for as long as it was."""
    for origin in (portal.ORIGIN_AGENT, portal.ORIGIN_CHAT):
        assert (portal.can(portal.MEMBER, "connector:pair_chat", origin)
                == portal.can(portal.MEMBER, "connector:disconnect_own", origin))


def test_the_admin_list_cannot_be_saved_empty(portal):
    """Found by an independent acceptance pass.

    A stored value beats the environment fallback by design, so one blank save
    took the box from "alex is an admin" to nobody is — Operations unreachable
    for everyone, no confirmation in front of it, and no route back through the
    UI. Recovery meant hand-editing settings.json on the box.
    """
    settings = sys.modules["agentbox_settings"]
    portal.SETTINGS.save({"admins": "alex"})
    for blank in ("", "   ", " , , ", "\n"):
        with pytest.raises(settings.InvalidSetting) as caught:
            portal.SETTINGS.save({"admins": blank})
        assert caught.value.key == "admins"
    # And the refusal changed nothing.
    assert portal.SETTINGS.value("admins") == "alex"
    assert portal.admin_names() == {"alex"}


def test_handing_over_is_still_allowed(portal):
    """The guard is against abolishing administration, not transferring it —
    a non-empty list saves even when it drops the person saving it."""
    portal.SETTINGS.save({"admins": "alex"})
    portal.SETTINGS.save({"admins": "sam"})
    assert portal.admin_names() == {"sam"}
    assert portal.role_of("alex") == portal.MEMBER


def test_absent_still_means_nobody(portal, tmp_path, monkeypatch):
    """The original fail-safe survives: a missing or garbled setting removes
    privilege rather than granting it. Only *saving* empty is refused."""
    settings = sys.modules["agentbox_settings"]
    store = settings.SettingsStore(directory=tmp_path / "fresh", environ={})
    assert store.value("admins") == ""


def test_a_stored_secret_can_be_cleared(portal):
    """Blank means keep, so without an explicit clear a stored secret could be
    replaced forever and removed never."""
    portal.SETTINGS.save({"smtp_password": "hunter2"})
    portal.SETTINGS.save({"smtp_password": ""})
    assert portal.SETTINGS.value("smtp_password") == "hunter2"
    portal.SETTINGS.save({"smtp_password": ""},
                         clear=frozenset({"smtp_password"}))
    assert portal.SETTINGS.value("smtp_password") == ""


def test_the_form_offers_the_clear_only_when_there_is_something_to_clear(portal):
    portal.SETTINGS.save({"smtp_password": "hunter2"})
    assert "clear_smtp_password" in portal.render_admin("alex", "").decode()
    portal.SETTINGS.save({"smtp_password": ""},
                         clear=frozenset({"smtp_password"}))
    assert "clear_smtp_password" not in portal.render_admin("alex", "").decode()


def test_expired_credentials_are_reaped(portal, tmp_path):
    """Each record holds an identity and a secret hash; keeping one past the
    point where it can authorise anything is surface with no purpose."""
    import json
    live = portal.now() + 3600
    for name, record in (
        ("sessions/dead", {"identity": "alex", "expires_at": portal.now() - 1}),
        ("sessions/live", {"identity": "alex", "expires_at": live}),
        ("links/expired", {"identity": "alex", "expires_at": portal.now() - 1}),
        ("links/spent", {"identity": "alex", "expires_at": live,
                         "used_at": portal.now() - 5}),
        ("links/usable", {"identity": "alex", "expires_at": live,
                          "used_at": None}),
    ):
        path = portal.STATE / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")

    sessions, links = portal.reap_expired()
    assert (sessions, links) == (1, 2)
    assert (portal.STATE / "sessions/live.json").exists()
    assert (portal.STATE / "links/usable.json").exists()
    assert not (portal.STATE / "links/spent.json").exists()
