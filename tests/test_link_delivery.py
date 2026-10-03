"""Getting a sign-in link to a person, without an SMTP credential.

The link is bound to the browser that asked for it — a nonce cookie set at
request time, required at redemption. That is what makes delivery over a
channel the assistant can read safe: reading the link is not enough to use it.
The property belongs to the portal, and if it were ever relaxed, Discord
delivery would have to stop with it.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from conftest import set_everywhere

REPO = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    loader = importlib.machinery.SourceFileLoader(name, str(REPO / "cli" / filename))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def portal(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOX_PORTAL_DIR", str(tmp_path / "portal"))
    monkeypatch.setenv("AGENTBOX_IDENTITY_EMAILS", "sam:sam@example.com")
    # Deliberately no AGENTBOX_DISCORD_IDENTITIES: the env var is the legacy
    # path, and a fixture that sets it globally hides whether the pairing
    # store actually works.
    monkeypatch.delenv("AGENTBOX_DISCORD_IDENTITIES", raising=False)
    monkeypatch.delenv("AGENTBOX_SMTP_HOST", raising=False)
    return _load("portal_delivery", "agentbox-portal")


@pytest.fixture
def approvals(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOX_PORTAL_DIR", str(tmp_path / "portal"))
    monkeypatch.delenv("AGENTBOX_DISCORD_IDENTITIES", raising=False)
    return _load("approvals_delivery", "agentbox-approvals")


def link_account(portal, identity="sam", user_id="99887766"):
    """Pair somebody the way the bot does, for tests about what follows."""
    data = portal.load_chat_links()
    data["linked"][identity] = {"user_id": user_id, "linked_at": 1}
    portal.save_chat_links(data)


# --- the portal side -----------------------------------------------------------


def test_a_link_is_spooled_for_discord_when_no_smtp_exists(portal):
    """The whole point: a household with no mail credential can still invite
    somebody."""
    link_account(portal)
    assert portal.deliver_link("sam", "sam@example.com", "http://box/login?x=1")
    spooled = portal.pending_requests("sam")
    assert len(spooled) == 1
    assert spooled[0]["action"] == "deliver_link"
    assert spooled[0]["url"] == "http://box/login?x=1"


def test_the_portal_never_holds_the_discord_token(portal):
    """Same split as the OAuth code: a LAN-reachable page must not hold a
    credential that can message the household."""
    from conftest import portal_code
    source = portal_code()
    assert "DISCORD_BOT_TOKEN" not in source
    assert "discord.com/api" not in source


def test_an_identity_with_no_discord_id_is_not_spooled(portal, monkeypatch):
    """Spooling for somebody unreachable would leave a link sitting on disk
    that nobody collects."""
    monkeypatch.setattr(portal, "discord_identities", lambda: set())
    assert portal.deliver_link("sam", "sam@example.com", "http://box/x") is False
    assert portal.pending_requests("sam") == []


def test_the_spooled_link_is_not_world_readable(portal):
    link_account(portal)
    portal.deliver_link("sam", "sam@example.com", "http://box/login?x=1")
    path = portal.request_path(portal.pending_requests("sam")[0]["id"])
    assert path.stat().st_mode & 0o777 == 0o600


# --- the operator side ---------------------------------------------------------


def test_the_operator_process_delivers_and_spends_the_link(approvals, portal):
    link_account(portal)
    portal.deliver_link("sam", "sam@example.com", "http://box/login?x=1")
    sent = []

    def fake_discord(method, path, token, payload=None):
        if path == "/users/@me/channels":
            return {"id": "dm-1"}
        sent.append((path, (payload or {}).get("content", "")))
        return {"id": "posted"}

    set_everywhere(approvals, "discord", fake_discord)
    approvals.deliver_pending_links("tok")
    assert sent and "http://box/login?x=1" in sent[0][1]
    # The URL is a credential until it expires; it is dropped once sent.
    done = json.loads(portal.request_path(
        [p["id"] for p in json.loads(json.dumps([
            {"id": e.stem} for e in (portal.STATE / "requests").glob("*.json")]))][0]
    ).read_text())
    assert "url" not in done
    assert done["completed_at"]


def test_the_dm_states_the_limit_accurately(approvals, portal):
    """It is going into a channel the assistant can read, so it must say what
    opening it there actually gets you — which is a session that can read and
    not change. Claiming the link is useless elsewhere would be false now that
    a mismatched nonce downgrades rather than refuses."""
    link_account(portal)
    portal.deliver_link("sam", "sam@example.com", "http://box/login?x=1")
    sent = []
    set_everywhere(approvals, "discord", lambda m, p, t, payload=None: (
        {"id": "dm-1"} if p == "/users/@me/channels"
        else (sent.append((payload or {}).get("content", "")) or {"id": "ok"})))
    approvals.deliver_pending_links("tok")
    assert "not approve or disconnect anything" in sent[0]
    assert "That includes me." in sent[0]


def test_a_link_spooled_before_a_disconnect_is_not_delivered(approvals, portal,
                                                             capsys):
    """Somebody disconnects Discord between requesting a link and the loop
    picking it up. The link must not follow them to an account they have just
    detached."""
    link_account(portal)
    portal.deliver_link("sam", "sam@example.com", "http://box/x")
    portal.unlink_chat("sam")
    set_everywhere(approvals, "discord", lambda *a, **k: pytest.fail("must not send"))
    approvals.deliver_pending_links("tok")
    assert "no Discord id" in capsys.readouterr().out


def test_a_completed_request_is_not_delivered_twice(approvals, portal):
    link_account(portal)
    portal.deliver_link("sam", "sam@example.com", "http://box/login?x=1")
    calls = []
    set_everywhere(approvals, "discord", lambda m, p, t, payload=None: (
        {"id": "dm-1"} if p == "/users/@me/channels"
        else (calls.append(1) or {"id": "ok"})))
    approvals.deliver_pending_links("tok")
    approvals.deliver_pending_links("tok")
    assert len(calls) == 1


# --- the operations surface ----------------------------------------------------


def test_operations_shows_which_channels_will_actually_deliver(portal):
    link_account(portal)
    """Delivery failure is invisible by construction: the sign-in page must
    answer identically for a registered and an unregistered address, so it can
    never say "that went nowhere". This page is the only place a person finds
    out."""
    body = portal.render_admin("alex", "").decode()
    assert "How sign-in links are delivered" in body
    assert "Discord DM to sam" in body


def test_it_says_plainly_when_nothing_is_configured(portal, monkeypatch):
    monkeypatch.setattr(portal.SETTINGS, "value",
                        lambda key: "" if key.startswith("smtp") else "")
    monkeypatch.setattr(portal, "discord_identities", lambda: set())
    body = portal.render_admin("alex", "").decode()
    assert "reach nobody" in body
    assert "agentbox-portal link" in body


def test_email_is_listed_when_smtp_is_set(portal, monkeypatch):
    portal.SETTINGS.save({"smtp_host": "smtp.gmail.com",
                          "smtp_user": "agentbox@example.com"})
    body = portal.render_admin("alex", "").decode()
    assert "smtp.gmail.com" in body
    assert "agentbox@example.com" in body


# --- opening a link somewhere other than where it was asked for ----------------


def test_a_link_opened_in_another_browser_still_signs_you_in(portal):
    """The ordinary case on a phone, not an attack.

    Discord and most mail apps open links in their own in-app browser, which
    has its own cookie jar, so the nonce set when the link was requested is
    not there. Refusing outright made delivery useless to anyone not sitting
    at the desktop browser they started from.
    """
    url, link_id = portal.mint_link("sam", "http://box", request_nonce="asked-here")
    identity, origin, reason = portal.redeem_link(
        link_id, url.split("k=")[1], request_nonce="")
    assert identity == "sam"
    assert reason == ""
    assert origin == portal.ORIGIN_CHAT


def test_but_it_cannot_approve_a_memory(portal):
    """The binding decides the privilege, not the access. Whoever merely read
    the message could be the one opening it."""
    assert not portal.can(portal.MEMBER, "memory:decide_own", portal.ORIGIN_CHAT)
    assert not portal.can(portal.MEMBER, "connector:disconnect_own",
                          portal.ORIGIN_CHAT)
    # Reading is fine — it grants nothing the assistant could not already do.
    assert portal.can(portal.MEMBER, "memory:read_own", portal.ORIGIN_CHAT)
    assert portal.can(portal.MEMBER, "connector:read_own", portal.ORIGIN_CHAT)


def test_the_same_browser_still_gets_everything(portal):
    url, link_id = portal.mint_link("sam", "http://box", request_nonce="asked-here")
    identity, origin, reason = portal.redeem_link(
        link_id, url.split("k=")[1], request_nonce="asked-here")
    assert (identity, reason) == ("sam", "")
    assert portal.can(portal.MEMBER, "memory:decide_own", origin)


def test_the_page_explains_the_limit_and_how_to_lift_it(portal):
    portal.own_proposals = lambda identity, role: []
    portal.memory_call = lambda *a, **k: {"memories": []}
    body = portal.render_home("sam", portal.MEMBER, "", portal.ORIGIN_CHAT).decode()
    assert "Opened in a different browser" in body
    assert "Phone apps usually open links in their own browser" in body
    assert "Send me a link for this browser" in body


# --- pairing a chat account from the page --------------------------------------
#
# Who receives a sign-in link by DM was an environment variable, so adding a
# person meant an operator editing a unit file and restarting a service. That
# put the household's job in the operator's hands for no security benefit.


def test_a_person_can_start_pairing_themselves(portal):
    code = portal.start_pairing("sam")
    assert len(code) == 6
    # Read off a screen and typed into a phone: no 0/O or 1/I.
    assert not (set(code) & set("01OI"))
    pending = portal.load_chat_links()["pending"]
    assert pending[code]["identity"] == "sam"


def test_starting_again_replaces_the_previous_code(portal):
    first = portal.start_pairing("sam")
    second = portal.start_pairing("sam")
    pending = portal.load_chat_links()["pending"]
    assert second in pending and first not in pending


def test_the_mapping_is_not_world_readable(portal):
    portal.start_pairing("sam")
    assert portal.chat_links_path().stat().st_mode & 0o777 == 0o600


def test_an_env_var_configured_box_still_works(portal, monkeypatch):
    """Boxes set up before pairing existed must not break."""
    monkeypatch.setenv("AGENTBOX_DISCORD_IDENTITIES", "sam:555")
    assert portal.chat_account_for("sam") == "555"
    assert "sam" in portal.discord_identities()


def test_a_paired_account_beats_nothing_and_survives_unlink(portal):
    assert portal.chat_account_for("sam") == ""
    data = portal.load_chat_links()
    data["linked"]["sam"] = {"user_id": "999", "linked_at": 1}
    portal.save_chat_links(data)
    assert portal.chat_account_for("sam") == "999"
    portal.unlink_chat("sam")
    assert portal.chat_account_for("sam") == ""


def test_the_bot_completes_a_pairing_from_a_dm(approvals, portal):
    """Receiving the code from that account is the proof. Anyone can type a
    user id into a form; only its holder can send a message from it."""
    code = portal.start_pairing("sam")
    sent = []

    def fake(method, path, token, payload=None):
        if path == "/users/@me/channels":
            return [{"id": "dm-7"}]
        if "messages" in path and method == "GET":
            return [{"content": f"link {code}",
                     "author": {"id": "424242", "bot": False}}]
        sent.append((payload or {}).get("content", ""))
        return {"id": "ok"}

    set_everywhere(approvals, "discord", fake)
    approvals.complete_pairings("tok")
    assert portal.chat_account_for("sam") == "424242"
    assert "You are **sam**" in sent[0]


def test_a_bot_cannot_pair_itself(approvals, portal):
    """The assistant is in the same Discord. If it could answer its own
    pairing code it would redirect somebody's sign-in links to itself."""
    code = portal.start_pairing("sam")
    set_everywhere(approvals, "discord", lambda method, path, token, payload=None: (
        [{"id": "dm-7"}] if path == "/users/@me/channels"
        else [{"content": f"link {code}", "author": {"id": "666", "bot": True}}]
        if method == "GET" else {"id": "ok"}))
    approvals.complete_pairings("tok")
    assert portal.chat_account_for("sam") == ""


def test_an_expired_code_does_not_pair(approvals, portal):
    code = portal.start_pairing("sam")
    data = portal.load_chat_links()
    data["pending"][code]["expires_at"] = 1
    portal.save_chat_links(data)
    set_everywhere(approvals, "discord", lambda method, path, token, payload=None: (
        [{"id": "dm-7"}] if path == "/users/@me/channels"
        else [{"content": f"link {code}", "author": {"id": "424242", "bot": False}}]
        if method == "GET" else {"id": "ok"}))
    approvals.complete_pairings("tok")
    assert portal.chat_account_for("sam") == ""


def test_a_wrong_code_does_not_pair(approvals, portal):
    portal.start_pairing("sam")
    set_everywhere(approvals, "discord", lambda method, path, token, payload=None: (
        [{"id": "dm-7"}] if path == "/users/@me/channels"
        else [{"content": "link ZZZZZZ", "author": {"id": "424242", "bot": False}}]
        if method == "GET" else {"id": "ok"}))
    approvals.complete_pairings("tok")
    assert portal.chat_account_for("sam") == ""


def test_the_accounts_page_offers_the_connect_button(portal):
    body = portal.render_connectors("sam", portal.MEMBER, "").decode()
    assert "Connect Discord" in body
    portal.start_pairing("sam")
    body = portal.render_connectors("sam", portal.MEMBER, "").decode()
    assert "link " in body and "Agentbox bot" in body
