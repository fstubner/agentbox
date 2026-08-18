"""Reading and writing are not the same risk, and the origins are not either.

`AGENT_WITHHELD` covers agent- and chat-minted links together because for
*writes* they are the same: neither proves the person acting is the person who
asked. Reading is where they part company, and conflating them was costing
something real — an admin could not check on the box from their phone, in
order to keep Operations from the assistant.
"""
from __future__ import annotations

import pytest
from conftest import code_of
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
    """A chat link is downgraded because it was not opened in the browser that
    asked for it — not because anyone untrusted is holding it."""
    assert portal.can(portal.ADMIN, "ops:read_health", portal.ORIGIN_CHAT)


def test_writes_stay_withheld_from_both(portal):
    """The split is about reading. Nothing about it loosens a write."""
    for origin in (portal.ORIGIN_AGENT, portal.ORIGIN_CHAT):
        for capability in ("ops:write_settings", "ops:write_household",
                           "ops:invite", "connector:pair_chat",
                           "connector:disconnect_own", "memory:decide_own"):
            assert not portal.can(portal.ADMIN, capability, origin), (
                capability, origin)


def test_an_unknown_origin_is_treated_as_the_assistant(portal):
    """It arrived from disk. The safe reading is the least trusted one."""
    assert not portal.can(portal.ADMIN, "ops:read_health", "something-else")
    assert not portal.can(portal.ADMIN, "ops:read_health", "")


def test_the_admin_route_passes_the_session_origin(portal):
    """The actual defect. Every capability check takes two gates and this
    route silently took the default for one of them, which is why a link the
    assistant minted could read Operations at all.
    """
    source = code_of("cli/agentbox-portal")
    route = source.split('parsed.path == "/admin"')[1][:600]
    assert "ops:read_health" in route
    assert 'session.get("origin"' in route


def test_the_refusal_names_the_reason_and_the_way_out(portal):
    key = portal.refusal(portal.ADMIN, "ops:read_health", portal.ORIGIN_AGENT)
    assert key == "agent_link_cannot_read_ops"
    text = portal.flash_text(key)
    assert text
    # It has to say how to get one that works, or it is a dead end.
    assert "link" in text.lower()


def test_a_chat_link_is_not_refused_at_all(portal):
    assert portal.refusal(portal.ADMIN, "ops:read_health",
                          portal.ORIGIN_CHAT) == ""


# --- the mechanical route ------------------------------------------------------


def test_the_bot_mints_chat_origin_never_operator(portal):
    """It arrives over a channel the assistant can read.

    An operator-origin link delivered that way would hand full privilege to
    anything with read access to Discord.
    """
    source = code_of("cli/agentbox-approvals")
    body = source.split("def send_link_on_request")[1][:2500]
    assert '"--origin", "chat"' in body
    assert "operator" not in body.split("subprocess.run")[1][:200]


def test_only_a_paired_account_gets_a_link(portal):
    """Pairing is what maps a Discord account to an identity; without it there
    is nobody to mint for."""
    source = code_of("cli/agentbox-approvals")
    body = source.split("def send_link_on_request")[1][:2500]
    assert "by_user" in body
    assert 'author.get("bot")' in body


def test_the_bot_does_not_answer_the_same_message_forever(portal):
    """The poll is five seconds. Without a cursor a standing `link` DM mints
    until the hourly cap and leaves a channel full of dead links."""
    source = code_of("cli/agentbox-approvals")
    body = source.split("def send_link_on_request")[1][:2500]
    assert "link_cursor()" in body
    assert "remember_link_request" in body


def test_the_portal_cli_defaults_to_operator(portal):
    """Only the bot asks for chat origin. A human at a terminal handing a link
    over directly is the case operator origin exists for."""
    source = code_of("cli/agentbox-portal")
    assert 'default=ORIGIN_OPERATOR' in source


def test_a_chat_link_is_not_told_the_assistant_made_it(portal):
    """Found by an independent acceptance pass.

    Both origins are downgraded, for different reasons. Somebody who DMed the
    bot `link` was told "this link was created by the assistant" — untrue, and
    the remedy it implies (ask for your own link) is the thing they just did.
    """
    for action in ("decide", "forget", "disconnect", "pair"):
        chat = portal.flash_text(portal.origin_refusal(portal.ORIGIN_CHAT, action))
        agent = portal.flash_text(portal.origin_refusal(portal.ORIGIN_AGENT, action))
        assert chat and agent and chat != agent, action
        assert "created by the assistant" not in chat, action
        assert "different browser" in chat, action


def test_the_nav_does_not_offer_a_tab_that_always_refuses(portal):
    """An admin on an assistant-minted link cannot read Operations, so
    offering the tab teaches people the nav lies."""
    assert "Operations" not in portal.chrome(
        "alex", portal.ADMIN, portal.ORIGIN_AGENT, "/", "")
    # Every origin that can actually open it still gets it.
    for origin in (portal.ORIGIN_EMAIL, portal.ORIGIN_OPERATOR, portal.ORIGIN_CHAT):
        assert "Operations" in portal.chrome(
            "alex", portal.ADMIN, origin, "/", ""), origin
    assert "Operations" not in portal.chrome(
        "sam", portal.MEMBER, portal.ORIGIN_EMAIL, "/", "")


def test_a_link_id_that_is_not_an_id_is_refused(portal):
    """link_id arrives from the query string of an unauthenticated request and
    went into a filesystem path unchecked."""
    for bad in ("../../etc/passwd", "a/b", "", "x" * 65, "id with space"):
        with pytest.raises(ValueError):
            portal.link_path(bad)
    assert portal.link_path("aBc-123_XYZ").name == "aBc-123_XYZ.json"
