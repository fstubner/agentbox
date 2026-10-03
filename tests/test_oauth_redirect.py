"""Where people reach the portal, and what Google is told, are two questions.

One setting answered both for a long time, and their requirements cannot both
be met. `AGENTBOX_PORTAL_URL` goes into sign-in links and invitations, so it
has to resolve from a phone. Google refuses a private IP or a `.local` name as
a redirect URI and accepts loopback — which on somebody's phone is their
phone.

Pointed at a name a phone can reach, consent broke. Pointed at loopback, every
delivered link went nowhere. That collision is why this looked like it needed
Tailscale or a domain to adopt at all, and splitting it is what makes a box
with no infrastructure work.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys

from conftest import REPO_ROOT as REPO
from conftest import portal_code


def load(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("AGENTBOX_PORTAL_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTBOX_ADMINS", "alex")
    for key in ("AGENTBOX_PORTAL_URL", "AGENTBOX_OAUTH_REDIRECT_BASE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_loader(
        "agentbox_portal", importlib.machinery.SourceFileLoader(
            "agentbox_portal", str(REPO / "cli" / "agentbox-portal")))
    module = importlib.util.module_from_spec(spec)
    sys.modules["agentbox_portal"] = module
    spec.loader.exec_module(module)
    return module


def test_the_public_url_no_longer_decides_the_redirect(tmp_path, monkeypatch):
    """The actual defect. Setting the portal to something a phone can reach
    silently moved the redirect URI to a value Google rejects."""
    portal = load(tmp_path, monkeypatch,
                  AGENTBOX_PORTAL_URL="http://agentbox.local:8771")
    assert portal.oauth_redirect_uri() == "http://127.0.0.1:8771/google/callback"


def test_the_default_is_the_one_that_always_works(tmp_path, monkeypatch):
    """Loopback needs no domain, no TLS and no VPN — it means consent is
    granted in a browser on this box, which for a household is somebody
    sitting down at it once."""
    portal = load(tmp_path, monkeypatch)
    assert portal.oauth_redirect_uri().startswith("http://127.0.0.1")


def test_a_household_with_a_hostname_can_set_one(tmp_path, monkeypatch):
    """The upgrade path is one setting, not a different deployment."""
    portal = load(tmp_path, monkeypatch,
                  AGENTBOX_PORTAL_URL="https://box.example.com",
                  AGENTBOX_OAUTH_REDIRECT_BASE="https://box.example.com")
    assert portal.oauth_redirect_uri() == "https://box.example.com/google/callback"


def test_a_trailing_slash_does_not_produce_a_double(tmp_path, monkeypatch):
    portal = load(tmp_path, monkeypatch,
                  AGENTBOX_OAUTH_REDIRECT_BASE="https://box.example.com/")
    assert portal.oauth_redirect_uri() == "https://box.example.com/google/callback"


def test_both_halves_of_the_flow_send_the_same_string():
    """Google compares the redirect URI at consent with the one at token
    exchange, and both with what is registered. Two call sites building it
    separately is how they drift — and the drift only shows up as a refusal
    from Google, minutes later, in somebody else's browser.
    """
    source = portal_code()
    assert source.count("oauth_redirect_uri()") >= 2
    # Neither may go back to deriving it from the public URL.
    assert "PUBLIC_URL.rstrip('/')}/google/callback" not in source
    assert 'PUBLIC_URL.rstrip("/")}/google/callback' not in source


def test_the_portal_unit_sets_a_name_a_phone_can_resolve(tmp_path, monkeypatch):
    """Loopback here meant every invitation delivered by email pointed the
    recipient at their own device."""
    unit = (REPO / "cli" / "agentbox-portal.service").read_text(encoding="utf-8")
    body = "\n".join(line for line in unit.splitlines()
                     if not line.lstrip().startswith("#"))
    assert "AGENTBOX_PORTAL_URL=http://127.0.0.1" not in body
    assert "AGENTBOX_PORTAL_URL=" in body
    assert "AGENTBOX_OAUTH_REDIRECT_BASE=" in body
