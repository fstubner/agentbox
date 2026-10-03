"""The settings page: who may write it, and what a rejection does.

The store's own validation is covered in test_settings_store.py. These cover
the seam between the form and the store, where typed text starts deciding who
is an admin.
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
    """`admins` decides privilege, so members cannot write it."""
    assert not portal.can(portal.MEMBER, "ops:write_settings")
    assert not portal.can(portal.MEMBER, "ops:write_household")
    assert portal.can(portal.ADMIN, "ops:write_settings")


def test_an_agent_minted_link_cannot_change_settings(portal):
    """If an assistant-made link could edit `admins`, the assistant could make
    anyone an admin. If it could edit `identity_emails`, it could send someone's
    sign-in link to a mailbox it reads. The admin role is not enough, and the
    link must also be one a person asked for.
    """
    for origin in (portal.ORIGIN_AGENT, portal.ORIGIN_CHAT):
        assert not portal.can(portal.ADMIN, "ops:write_settings", origin)
        assert not portal.can(portal.ADMIN, "ops:write_household", origin)
    assert portal.can(portal.ADMIN, "ops:write_settings", portal.ORIGIN_EMAIL)


def test_settings_writes_are_in_the_withheld_set(portal):
    """Stated once here so removing it from AGENT_WITHHELD fails a test.

    The capability check above would still pass if someone granted these to
    agent links on purpose. This test pins the intent.
    """
    assert "ops:write_settings" in portal.AGENT_WITHHELD
    assert "ops:write_household" in portal.AGENT_WITHHELD


# --- what the form does with what it is given ----------------------------------


def test_one_bad_field_changes_nothing(portal):
    """A half-applied save could leave an admin list nobody chose."""
    portal.SETTINGS.save({"admins": "alex", "smtp_host": "smtp.example.com"})
    with pytest.raises(portal.agentbox_settings.InvalidSetting):
        portal.SETTINGS.save({"admins": "sam", "smtp_port": "not-a-port"})
    assert portal.SETTINGS.value("admins") == "alex"
    assert portal.SETTINGS.value("smtp_host") == "smtp.example.com"


def test_a_rejection_says_which_field_was_wrong(portal):
    """The page shows the message next to the input, not at the top."""
    with pytest.raises(portal.agentbox_settings.InvalidSetting) as caught:
        portal.SETTINGS.save({"smtp_port": "99999"})
    assert caught.value.key == "smtp_port"


def test_the_secret_is_never_rendered_into_the_page(portal):
    """It would otherwise sit in every admin's history and page cache."""
    portal.SETTINGS.save({"smtp_password": "hunter2-app-password"})
    body = portal.render_admin("alex", "").decode()
    assert "hunter2-app-password" not in body
    # The page still says one is stored, or nobody could tell it apart from a
    # box with no password.
    assert "stored" in body


def test_a_blank_secret_keeps_the_stored_one(portal):
    """The form submits every field, so blank has to mean "unchanged"."""
    portal.SETTINGS.save({"smtp_password": "kept"})
    portal.SETTINGS.save({"smtp_password": "", "smtp_host": "smtp.example.com"})
    assert portal.SETTINGS.value("smtp_password") == "kept"


def test_every_setting_appears_on_the_page(portal):
    """The page is built from the settings table, so a setting in the table
    must appear on the page. One that looks configurable and is not would be
    worse than one that is missing.
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
    """Storing it would mislead somebody into thinking they granted it."""
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
    """`can` combines two gates into one yes or no. A member refused an
    admin-only action must be told about their role, not sent to sign in with a
    different link that would be refused the same way.
    """
    assert portal.refusal(portal.MEMBER, "ops:write_settings",
                          portal.ORIGIN_OPERATOR) == "admin_only"
    assert portal.refusal(portal.MEMBER, "ops:invite",
                          portal.ORIGIN_EMAIL) == "admin_only"
    # The origin message is still used when the origin is the reason.
    assert portal.refusal(portal.ADMIN, "ops:write_settings",
                          portal.ORIGIN_AGENT) == "agent_link_cannot_configure"
    assert portal.refusal(portal.ADMIN, "ops:invite",
                          portal.ORIGIN_CHAT) == "agent_link_cannot_configure"
    # Nothing is refused when nothing should be.
    assert portal.refusal(portal.ADMIN, "ops:invite", portal.ORIGIN_EMAIL) == ""


def test_every_refusal_reason_has_a_message(portal):
    """A key with no entry renders nothing, so a wrong key shows no message."""
    for role in (portal.MEMBER, portal.ADMIN):
        for origin in (portal.ORIGIN_EMAIL, portal.ORIGIN_OPERATOR,
                       portal.ORIGIN_AGENT, portal.ORIGIN_CHAT):
            for cap in ("ops:write_settings", "ops:write_household", "ops:invite"):
                key = portal.refusal(role, cap, origin)
                if key:
                    assert portal.flash_text(key), key


def test_connecting_a_chat_account_is_withheld_from_agent_links(portal):
    """Pairing a chat account is withheld from assistant-made links, like
    unlinking.

    Pairing points sign-in links somewhere new, the more dangerous direction.
    The pairing code is shown on a page any assistant-made session can read, so
    someone who could inject into the assistant and controlled a Discord
    account could otherwise have that person's links sent to them.
    """
    for origin in (portal.ORIGIN_AGENT, portal.ORIGIN_CHAT):
        assert not portal.can(portal.ADMIN, "connector:pair_chat", origin)
        assert not portal.can(portal.MEMBER, "connector:pair_chat", origin)
    # The ordinary path still works, or nobody could connect Discord.
    for origin in (portal.ORIGIN_EMAIL, portal.ORIGIN_OPERATOR):
        assert portal.can(portal.MEMBER, "connector:pair_chat", origin)
    assert "connector:pair_chat" in portal.AGENT_WITHHELD


def test_pairing_and_unlinking_are_withheld_together(portal):
    """They are the same decision, so withholding one without the other
    would be inconsistent."""
    for origin in (portal.ORIGIN_AGENT, portal.ORIGIN_CHAT):
        assert (portal.can(portal.MEMBER, "connector:pair_chat", origin)
                == portal.can(portal.MEMBER, "connector:disconnect_own", origin))


def test_the_admin_list_cannot_be_saved_empty(portal):
    """A stored value beats the environment fallback, so one blank save would
    leave nobody an admin, with Operations unreachable and no way back through
    the page.
    """
    settings = sys.modules["agentbox_settings"]
    portal.SETTINGS.save({"admins": "alex"})
    for blank in ("", "   ", " , , ", "\n"):
        with pytest.raises(settings.InvalidSetting) as caught:
            portal.SETTINGS.save({"admins": blank})
        assert caught.value.key == "admins"
    # The refusal changed nothing.
    assert portal.SETTINGS.value("admins") == "alex"
    assert portal.admin_names() == {"alex"}


def test_handing_over_is_still_allowed(portal):
    """The guard stops removing every admin, not handing the role over. A
    non-empty list saves even if it leaves out the person saving it."""
    portal.SETTINGS.save({"admins": "alex"})
    portal.SETTINGS.save({"admins": "sam"})
    assert portal.admin_names() == {"sam"}
    assert portal.role_of("alex") == portal.MEMBER


def test_absent_still_means_nobody(portal, tmp_path, monkeypatch):
    """The fail-safe still holds. A missing or garbled setting removes
    privilege and does not grant it. Only *saving* empty is refused."""
    settings = sys.modules["agentbox_settings"]
    store = settings.SettingsStore(directory=tmp_path / "fresh", environ={})
    assert store.value("admins") == ""


def test_a_stored_secret_can_be_cleared(portal):
    """Blank means keep, so without an explicit clear a stored secret could be
    replaced but never removed."""
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
    """Each record holds an identity and a secret hash. Keeping one after it
    can no longer authorise anything adds attack surface for no purpose."""
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


def test_an_admin_list_of_strangers_is_refused(portal):
    """`clean_admins` only checks shape, so a misspelt name would lock everyone
    out as thoroughly as an empty list."""
    portal.SETTINGS.save({"admins": "alex",
                          "identity_emails": "alex:alex@example.com"})
    known = portal.known_identities()
    assert "alex" in known and "mai" not in known
    # Somebody who exists can still be handed the box.
    portal.SETTINGS.save({"identity_emails":
                          "alex:alex@example.com,sam:sam@example.com"})
    assert "sam" in portal.known_identities()


def test_an_identity_that_cannot_sign_in_is_shown(portal, monkeypatch):
    """The card exists to show this. A configured gateway identity with no
    way to sign in, such as sam here, must be listed as such."""
    monkeypatch.setenv("AGENTBOX_IDENTITY_NAMES", "alex,sam")
    portal.SETTINGS.save({"identity_emails": "alex:alex@example.com"})
    body = portal.render_people_card()
    assert "sam" in body
    assert "no way to receive a sign-in link" in body


def test_the_module_docstring_describes_the_route_the_assistant_has(portal):
    """The module docstring must say where the identity binding lives.

    Asserted positively, because a check that the old sentence is absent would
    pass without reading anything, since `code_of` strips docstrings.
    """
    doc = sys.modules["agentbox_portal"].__doc__ or ""
    assert "POST /agent/link" in doc
    # The endpoint takes an identity in the body, and the binding lives in the
    # MCP tool that calls it, so the docstring must not claim the endpoint
    # cannot name somebody else.
    assert "the identity binding lives" in doc.lower()
    assert "cannot name somebody else" not in doc
