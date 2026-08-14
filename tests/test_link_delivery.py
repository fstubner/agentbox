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
    monkeypatch.setenv("AGENTBOX_DISCORD_IDENTITIES", "sam:99887766")
    monkeypatch.delenv("AGENTBOX_SMTP_HOST", raising=False)
    return _load("portal_delivery", "agentbox-portal")


@pytest.fixture
def approvals(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOX_PORTAL_DIR", str(tmp_path / "portal"))
    monkeypatch.setenv("AGENTBOX_DISCORD_IDENTITIES", "sam:99887766")
    return _load("approvals_delivery", "agentbox-approvals")


# --- the portal side -----------------------------------------------------------


def test_a_link_is_spooled_for_discord_when_no_smtp_exists(portal):
    """The whole point: a household with no mail credential can still invite
    somebody."""
    assert portal.deliver_link("sam", "sam@example.com", "http://box/login?x=1")
    spooled = portal.pending_requests("sam")
    assert len(spooled) == 1
    assert spooled[0]["action"] == "deliver_link"
    assert spooled[0]["url"] == "http://box/login?x=1"


def test_the_portal_never_holds_the_discord_token(portal):
    """Same split as the OAuth code: a LAN-reachable page must not hold a
    credential that can message the household."""
    from conftest import code_of
    source = code_of("cli/agentbox-portal")
    assert "DISCORD_BOT_TOKEN" not in source
    assert "discord.com/api" not in source


def test_an_identity_with_no_discord_id_is_not_spooled(portal, monkeypatch):
    """Spooling for somebody unreachable would leave a link sitting on disk
    that nobody collects."""
    monkeypatch.setattr(portal, "discord_identities", lambda: set())
    assert portal.deliver_link("sam", "sam@example.com", "http://box/x") is False
    assert portal.pending_requests("sam") == []


def test_the_spooled_link_is_not_world_readable(portal):
    portal.deliver_link("sam", "sam@example.com", "http://box/login?x=1")
    path = portal.request_path(portal.pending_requests("sam")[0]["id"])
    assert path.stat().st_mode & 0o777 == 0o600


# --- the operator side ---------------------------------------------------------


def test_the_operator_process_delivers_and_spends_the_link(approvals, portal):
    portal.deliver_link("sam", "sam@example.com", "http://box/login?x=1")
    sent = []

    def fake_discord(method, path, token, payload=None):
        if path == "/users/@me/channels":
            return {"id": "dm-1"}
        sent.append((path, (payload or {}).get("content", "")))
        return {"id": "posted"}

    approvals.discord = fake_discord
    approvals.deliver_pending_links("tok")
    assert sent and "http://box/login?x=1" in sent[0][1]
    # The URL is a credential until it expires; it is dropped once sent.
    done = json.loads(portal.request_path(
        [p["id"] for p in json.loads(json.dumps([
            {"id": e.stem} for e in (portal.STATE / "requests").glob("*.json")]))][0]
    ).read_text())
    assert "url" not in done
    assert done["completed_at"]


def test_the_dm_says_the_link_is_useless_to_a_reader(approvals, portal):
    """It is going into a channel the assistant can read. Saying so is the
    honest thing, and it is also true."""
    portal.deliver_link("sam", "sam@example.com", "http://box/login?x=1")
    sent = []
    approvals.discord = lambda m, p, t, payload=None: (
        {"id": "dm-1"} if p == "/users/@me/channels"
        else (sent.append((payload or {}).get("content", "")) or {"id": "ok"}))
    approvals.deliver_pending_links("tok")
    assert "only in the browser you asked for it from" in sent[0]


def test_an_unmapped_identity_is_logged_not_delivered(approvals, portal, monkeypatch, capsys):
    portal.deliver_link("sam", "sam@example.com", "http://box/x")
    monkeypatch.setattr(approvals, "identity_discord_map", dict)
    approvals.discord = lambda *a, **k: pytest.fail("must not send")
    approvals.deliver_pending_links("tok")
    assert "no Discord id" in capsys.readouterr().out


def test_a_completed_request_is_not_delivered_twice(approvals, portal):
    portal.deliver_link("sam", "sam@example.com", "http://box/login?x=1")
    calls = []
    approvals.discord = lambda m, p, t, payload=None: (
        {"id": "dm-1"} if p == "/users/@me/channels"
        else (calls.append(1) or {"id": "ok"}))
    approvals.deliver_pending_links("tok")
    approvals.deliver_pending_links("tok")
    assert len(calls) == 1


# --- the operations surface ----------------------------------------------------


def test_operations_shows_which_channels_will_actually_deliver(portal):
    """Delivery failure is invisible by construction: the sign-in page must
    answer identically for a registered and an unregistered address, so it can
    never say "that went nowhere". This page is the only place a person finds
    out."""
    body = portal.render_admin("alex", "").decode()
    assert "How sign-in links are delivered" in body
    assert "Discord DM to sam" in body


def test_it_says_plainly_when_nothing_is_configured(portal, monkeypatch):
    monkeypatch.setattr(portal, "SMTP_HOST", "")
    monkeypatch.setattr(portal, "discord_identities", lambda: set())
    body = portal.render_admin("alex", "").decode()
    assert "reach nobody" in body
    assert "agentbox-portal link" in body


def test_email_is_listed_when_smtp_is_set(portal, monkeypatch):
    monkeypatch.setattr(portal, "SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(portal, "SMTP_USER", "agentbox@example.com")
    body = portal.render_admin("alex", "").decode()
    assert "smtp.gmail.com" in body
    assert "agentbox@example.com" in body
