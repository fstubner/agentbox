"""The tool that lets the assistant hand somebody their own sign-in link.

The thing most worth protecting here is the absence of an identity parameter.
"Who is the link for" looks exactly like an ordinary argument, and making it
one would quietly undo the property the whole gateway is built on: identity is
the credential, not a value the model can choose.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from conftest import code_of

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "services" / "compose" / "agentbox-mcp" / "app"


@pytest.fixture
def portal_tool(monkeypatch):
    monkeypatch.setenv("AGENTBOX_PORTAL_AGENT_TOKEN", "test-token")
    monkeypatch.setenv("AGENTBOX_PORTAL_INTERNAL_URL", "http://portal.test")
    for path in (APP, APP / "integrations",
                 REPO / "services" / "templates" / "mcp"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    spec = importlib.util.spec_from_file_location(
        "portal_integration", APP / "integrations" / "portal.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["portal_integration"] = module
    spec.loader.exec_module(module)
    return module


# --- the property that matters -------------------------------------------------


def test_the_schema_offers_no_identity(portal_tool):
    """A parameter the model cannot see is one an injected instruction cannot
    fill in."""
    tool = portal_tool.TOOLS[0]
    assert tool["name"] == "request_signin_link"
    assert tool["inputSchema"].get("properties") == {}
    assert not tool["inputSchema"].get("required")


def test_the_identity_comes_from_the_credential(portal_tool, monkeypatch):
    sent = {}

    def fake_urlopen(request, timeout=None):
        sent["body"] = request.data.decode()

        class R:
            def read(self):
                return b'{"url":"http://portal.test/login?id=x","limits":"..."}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R()

    monkeypatch.setattr(portal_tool.urllib.request, "urlopen", fake_urlopen)
    portal_tool.CURRENT_IDENTITY.set("sam")
    portal_tool.dispatch("request_signin_link", {})
    assert "identity=sam" in sent["body"]


def test_an_identity_argument_is_ignored_rather_than_honoured(portal_tool,
                                                              monkeypatch):
    """The injection case, verified live against the running gateway too:
    sam's token with `identity: alex` produced a link for sam."""
    sent = {}

    def fake_urlopen(request, timeout=None):
        sent["body"] = request.data.decode()

        class R:
            def read(self):
                return b'{"url":"http://portal.test/login?id=x"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R()

    monkeypatch.setattr(portal_tool.urllib.request, "urlopen", fake_urlopen)
    portal_tool.CURRENT_IDENTITY.set("sam")
    portal_tool.dispatch("request_signin_link", {"identity": "alex"})
    assert "identity=sam" in sent["body"]
    assert "alex" not in sent["body"]


def test_the_dispatch_never_reads_identity_from_arguments(portal_tool):
    """Stated against the source as well, because the test above would still
    pass if somebody added a fallback like `args.get("identity") or current`."""
    source = code_of(
        "services/compose/agentbox-mcp/app/integrations/portal.py")
    body = source.split("def dispatch")[1]
    assert "CURRENT_IDENTITY.get()" in body
    assert 'args.get("identity")' not in body
    assert 'args["identity"]' not in body


# --- refusing rather than guessing ---------------------------------------------


def test_no_identity_refuses_instead_of_picking_somebody(portal_tool):
    """Single-operator gateways have no identity. Guessing would mint a link
    for whoever happens to be first in a config file."""
    portal_tool.CURRENT_IDENTITY.set("")
    with pytest.raises(Exception) as caught:
        portal_tool.dispatch("request_signin_link", {})
    assert "identities" in str(caught.value)


def test_a_missing_token_says_so_plainly(portal_tool, monkeypatch):
    monkeypatch.setattr(portal_tool, "PORTAL_TOKEN", "")
    portal_tool.CURRENT_IDENTITY.set("alex")
    with pytest.raises(Exception) as caught:
        portal_tool.dispatch("request_signin_link", {})
    assert "AGENTBOX_PORTAL_AGENT_TOKEN" in str(caught.value)


def test_the_rate_cap_is_reported_as_an_answer_not_a_failure(portal_tool,
                                                             monkeypatch):
    """Otherwise the assistant retries into a wall."""
    import urllib.error

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 429, "Too many", {}, None)

    monkeypatch.setattr(portal_tool.urllib.request, "urlopen", fake_urlopen)
    portal_tool.CURRENT_IDENTITY.set("alex")
    with pytest.raises(Exception) as caught:
        portal_tool.dispatch("request_signin_link", {})
    assert "last hour" in str(caught.value)


# --- the tier ------------------------------------------------------------------


def test_it_is_allowed_rather_than_gated():
    """Putting an approval in front of somebody asking for their own sign-in
    link is the exact everyday prompt that trains people to approve unread.
    What keeps it safe is that the link is bounded, not that the mint is."""
    import yaml
    policy = yaml.safe_load(
        (REPO / "policies" / "approval-policy.yaml").read_text())
    tiers = policy.get("tiers", policy)
    assert "request_signin_link" in tiers["allowed"]
    assert "request_signin_link" not in tiers.get("approval_required", [])
    assert "request_signin_link" not in tiers.get("always_denied", [])
    assert policy["tools"]["request_signin_link"] == "request_signin_link"
