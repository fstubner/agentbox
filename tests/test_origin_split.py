"""How assistant-made and chat-delivered links differ for reading and writing.

`AGENT_WITHHELD` covers assistant-made and chat-delivered links together,
because for writes they are the same. Neither proves the person acting is the
one who asked. For reading they differ. An admin must be able to check the box
from their phone, while Operations stays hidden from the assistant.
"""
from __future__ import annotations

import pytest
from conftest import portal_code, script_code
from test_portal_auth import load_portal


@pytest.fixture
def portal(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOX_POLICY_DIR", str(tmp_path / "policy"))
    return load_portal(tmp_path, monkeypatch)


def test_the_assistant_cannot_read_operations(portal):
    """It lists who lives here, their sign-in addresses and Discord ids, and
    the assistant can read any link it mints."""
    assert not portal.can(portal.ADMIN, "ops:read_health", portal.ORIGIN_AGENT)


def test_a_phone_link_still_can(portal):
    """A chat link is downgraded because it was opened in a different browser,
    not because anyone untrusted holds it."""
    assert portal.can(portal.ADMIN, "ops:read_health", portal.ORIGIN_CHAT)


def test_writes_stay_withheld_from_both(portal):
    """The split only affects reading. It does not allow any write."""
    for origin in (portal.ORIGIN_AGENT, portal.ORIGIN_CHAT):
        for capability in ("ops:write_settings", "ops:write_household",
                           "ops:invite", "connector:pair_chat",
                           "connector:disconnect_own", "memory:decide_own"):
            assert not portal.can(portal.ADMIN, capability, origin), (
                capability, origin)


def test_an_unknown_origin_is_treated_as_the_assistant(portal):
    """The origin is read from disk, so an unknown value gets the least
    trusted reading."""
    assert not portal.can(portal.ADMIN, "ops:read_health", "something-else")
    assert not portal.can(portal.ADMIN, "ops:read_health", "")


def test_the_admin_route_passes_the_session_origin(portal):
    """Every capability check takes two gates. If this route took the default
    for the origin gate, a link the assistant minted could read Operations.
    """
    source = portal_code()
    route = source.split('parsed.path == "/admin"')[1][:600]
    assert "ops:read_health" in route
    assert 'session.get("origin"' in route


def test_the_refusal_names_the_reason_and_the_way_out(portal):
    key = portal.refusal(portal.ADMIN, "ops:read_health", portal.ORIGIN_AGENT)
    assert key == "agent_link_cannot_read_ops"
    text = portal.flash_text(key)
    assert text
    # It has to say how to get a link that works.
    assert "link" in text.lower()


def test_a_chat_link_is_not_refused_at_all(portal):
    assert portal.refusal(portal.ADMIN, "ops:read_health",
                          portal.ORIGIN_CHAT) == ""


# --- the mechanical route ------------------------------------------------------


def test_the_bot_mints_chat_origin_never_operator(portal):
    """The link arrives over a channel the assistant can read.

    An operator-origin link delivered that way would hand full privilege to
    anything with read access to Discord.
    """
    source = script_code("agentbox-approvals")
    body = source.split("def send_link_on_request")[1][:2500]
    assert '"--origin", "chat"' in body
    assert "operator" not in body.split("subprocess.run")[1][:200]


def test_only_a_paired_account_gets_a_link(portal):
    """Pairing maps a Discord account to an identity. Without it there is
    nobody to mint for."""
    source = script_code("agentbox-approvals")
    body = source.split("def send_link_on_request")[1][:2500]
    assert "by_user" in body
    assert 'author.get("bot")' in body


def test_the_bot_does_not_answer_the_same_message_forever(portal):
    """The loop polls every five seconds. Without a cursor, one `link` DM would
    be answered until the hourly cap."""
    source = script_code("agentbox-approvals")
    body = source.split("def send_link_on_request")[1][:2500]
    assert "link_cursor()" in body
    assert "remember_link_request" in body


def test_the_portal_cli_defaults_to_operator(portal):
    """Only the bot asks for chat origin. A human at a terminal handing a link
    over directly is the case operator origin exists for."""
    source = portal_code()
    assert 'default=config.ORIGIN_OPERATOR' in source


def test_a_chat_link_is_not_told_the_assistant_made_it(portal):
    """The message must name the right reason. A chat-delivered link is limited
    because of the browser, not because the assistant made it. Telling them
    to ask for their own link would send them to repeat what they just did.
    """
    for action in ("decide", "forget", "disconnect", "pair"):
        chat = portal.flash_text(portal.origin_refusal(portal.ORIGIN_CHAT, action))
        agent = portal.flash_text(portal.origin_refusal(portal.ORIGIN_AGENT, action))
        assert chat and agent and chat != agent, action
        assert "created by the assistant" not in chat, action
        assert "different browser" in chat, action


def test_the_nav_does_not_offer_a_tab_that_always_refuses(portal):
    """An admin on an assistant-minted link cannot read Operations, so the
    nav does not show a tab that would only refuse them."""
    assert "Operations" not in portal.chrome(
        "alex", portal.ADMIN, portal.ORIGIN_AGENT, "/", "")
    # Every origin that can open it still gets it.
    for origin in (portal.ORIGIN_EMAIL, portal.ORIGIN_OPERATOR, portal.ORIGIN_CHAT):
        assert "Operations" in portal.chrome(
            "alex", portal.ADMIN, origin, "/", ""), origin
    assert "Operations" not in portal.chrome(
        "sam", portal.MEMBER, portal.ORIGIN_EMAIL, "/", "")


def test_a_link_id_that_is_not_an_id_is_refused(portal):
    """link_id arrives from the query string of an unauthenticated request and
    becomes a filesystem path, so it is checked first."""
    for bad in ("../../etc/passwd", "a/b", "", "x" * 65, "id with space"):
        with pytest.raises(ValueError):
            portal.link_path(bad)
    assert portal.link_path("aBc-123_XYZ").name == "aBc-123_XYZ.json"


def test_a_malformed_link_id_answers_like_an_unknown_one(portal):
    """A malformed id must not raise.

    An exception would close the connection while an unknown id returns a page,
    which would tell the two apart. Malformed, missing and tampered ids must
    all get the same answer.
    """
    generic = portal.redeem_link("../../etc/passwd", "x")[2]
    for bad in ("a/b", "", "x" * 80, "id with space", "%2e%2e"):
        assert portal.redeem_link(bad, "x")[2] == generic, bad
    # A well-shaped id that does not exist gets the same answer.
    assert portal.redeem_link("aBc-123_XYZ", "x")[2] == generic
