"""Tests for the consolidated MCP gateway and its identity model.

The property that matters: **identity is the credential, not an argument.**
Whoever presents alex's token is alex, and there is no way to ask to be
someone else — so an instruction embedded in an email cannot switch accounts,
because switching would require a token the process was never given.

If that ever regresses, one compromised context reaches both people's mail
instead of one, which is the entire cost of going multi-user.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import code_of

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "services" / "compose" / "agentbox-mcp" / "app"
sys.path.insert(0, str(REPO / "services" / "templates" / "mcp"))
sys.path.insert(0, str(APP))

os.environ.setdefault("AGENTBOX_RUNTIME_POLICY",
                      str(REPO / "policies" / "approval-policy.yaml"))


def load_gateway(identities: str = ""):
    """Fresh module — identity_tokens is bound at class definition."""
    os.environ["AGENTBOX_IDENTITIES"] = identities
    os.environ["AGENTBOX_MCP_SHARED_TOKEN"] = "shared-secret"
    spec = importlib.util.spec_from_file_location(
        f"gw_{abs(hash(identities))}", APP / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gw():
    return load_gateway("alex:tok-alex,sam:tok-sam")


@pytest.fixture
def open_gate(monkeypatch):
    """Neutralise the policy gate for tests about *identity resolution*.

    policy_gate binds its paths as default arguments at import, and another
    test module imports it first with the in-container defaults — so every
    tool looks unmapped and is denied, dispatch never runs, and these tests
    would be asserting on a gate they are not about. test_policy_gate owns
    that behaviour.
    """
    import mcp_base
    monkeypatch.setattr(mcp_base.policy_gate, "check", lambda *a, **k: None)


def serve(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def rpc(base, method, params=None, token=None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params or {}}).encode()
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(base + "/mcp", data=body,
                                     headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


# --- assembly -----------------------------------------------------------------


def test_every_integration_contributes_tools(gw):
    """Derived from the registry rather than a hardcoded list.

    A snapshot here fails on every new integration and gets "fixed" by pasting
    the name in, which tests nothing. The property worth holding is that
    something registered contributes tools — a module wired in but silently
    exporting none is the bug this catches.
    """
    owners = set(gw.TOOL_OWNER.values())
    assert owners == set(gw.INTEGRATIONS)
    assert len(gw.TOOLS) == len(gw.TOOL_OWNER)
    assert len(owners) >= 5


def test_tool_names_are_unique_across_integrations(gw):
    """Two integrations claiming one name would make dispatch depend on dict
    ordering, silently routing a call to the wrong bridge."""
    names = [t["name"] for t in gw.TOOLS]
    assert len(names) == len(set(names))


def test_a_collision_refuses_to_start(gw, monkeypatch):
    class Fake:
        TOOLS = [{"name": "list_tasks"}]

        @staticmethod
        def dispatch(name, args):
            return None

    monkeypatch.setitem(gw.INTEGRATIONS, "impostor", Fake)
    with pytest.raises(SystemExit) as exc:
        gw._assemble()
    assert "collision" in str(exc.value)


def test_tools_are_ordered_deterministically(gw):
    """Prefix caching: the tool block sits at the front of every prompt and
    must be byte-identical between turns."""
    assert [t["name"] for t in gw.TOOLS] == sorted(t["name"] for t in gw.TOOLS)


def test_every_gateway_tool_is_mapped_to_a_capability(gw):
    import policy_gate as pg
    mapping = pg.load_tool_map(REPO / "policies" / "approval-policy.yaml")
    unmapped = [t["name"] for t in gw.TOOLS if t["name"] not in mapping]
    assert not unmapped, f"unmapped: {unmapped}"


# --- identity is the credential ------------------------------------------------


def test_the_token_selects_the_identity(gw, open_gate):
    server, base = serve(gw.AgentboxMcp)
    try:
        for token, expected in (("tok-alex", "alex"), ("tok-sam", "sam")):
            seen = {}
            gw.AgentboxMcp.dispatch = staticmethod(
                lambda n, a, _seen=seen: _seen.update(
                    who=gw._client.CURRENT_IDENTITY.get()))
            rpc(base, "tools/call",
                {"name": "list_tasks", "arguments": {}}, token=token)
            assert seen["who"] == expected
    finally:
        server.shutdown()


def test_an_unknown_token_is_refused(gw):
    server, base = serve(gw.AgentboxMcp)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            rpc(base, "tools/list", token="tok-nobody")
        assert exc.value.code == 401
    finally:
        server.shutdown()


def test_the_shared_token_does_not_work_once_identities_exist(gw):
    """Otherwise the single-operator token would be an unnamed sixth identity
    that every per-identity check silently ignores."""
    server, base = serve(gw.AgentboxMcp)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            rpc(base, "tools/list", token="shared-secret")
        assert exc.value.code == 401
    finally:
        server.shutdown()


def test_no_argument_can_set_the_identity(gw, open_gate):
    """The property the whole design rests on. A tool argument named
    `identity`, `as`, or anything else must not influence who the call acts
    as — otherwise injected content could name an account."""
    server, base = serve(gw.AgentboxMcp)
    try:
        seen = {}
        gw.AgentboxMcp.dispatch = staticmethod(
            lambda n, a: seen.update(who=gw._client.CURRENT_IDENTITY.get()))
        rpc(base, "tools/call", {"name": "list_tasks", "arguments": {
            "identity": "sam", "as": "sam", "_identity": "sam",
            "X-Agentbox-Identity": "sam"}}, token="tok-alex")
        assert seen["who"] == "alex"
    finally:
        server.shutdown()


def test_single_operator_still_works_with_no_identities(open_gate):
    """Every existing deployment has no identities configured and must keep
    behaving exactly as before."""
    gw = load_gateway("")
    server, base = serve(gw.AgentboxMcp)
    try:
        seen = {}
        gw.AgentboxMcp.dispatch = staticmethod(
            lambda n, a: seen.update(who=gw._client.CURRENT_IDENTITY.get()))
        rpc(base, "tools/call", {"name": "list_tasks", "arguments": {}},
            token="shared-secret")
        assert seen["who"] == ""
    finally:
        server.shutdown()


def test_unauthenticated_calls_are_still_refused(gw):
    server, base = serve(gw.AgentboxMcp)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            rpc(base, "tools/list")
        assert exc.value.code == 401
    finally:
        server.shutdown()


def test_an_identity_with_an_empty_token_cannot_authenticate():
    """`AGENTBOX_IDENTITIES=alex:` must not create an identity that an empty
    Authorization header satisfies."""
    gw = load_gateway("alex:,sam:tok-sam")
    assert "alex" not in gw.AgentboxMcp.identity_tokens
    server, base = serve(gw.AgentboxMcp)
    try:
        for bad in ("", "Bearer "):
            with pytest.raises(urllib.error.HTTPError):
                rpc(base, "tools/list", token=bad.replace("Bearer ", ""))
    finally:
        server.shutdown()


# --- routing --------------------------------------------------------------------


def test_identity_routes_to_a_per_identity_bridge(gw, monkeypatch):
    """Sam's mail must reach a bridge holding only her credential. The
    routing table decides, never the model."""
    monkeypatch.setenv("GOOGLE_BRIDGE_URL_SAM", "http://sam-google:8080")
    monkeypatch.setenv("GOOGLE_BRIDGE_TOKEN_SAM", "sam-bridge-token")
    monkeypatch.setenv("GOOGLE_BRIDGE_URL", "http://shared-google:8080")
    monkeypatch.setenv("GOOGLE_BRIDGE_TOKEN", "shared-bridge-token")
    client = gw._client.bridge_client("GOOGLE", "google-workspace-bridge")

    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["auth"] = request.headers.get("Authorization")
        captured["identity"] = request.headers.get("X-agentbox-identity")

        class R:
            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    monkeypatch.setattr(gw._client.urllib.request, "urlopen", fake_urlopen)

    gw._client.CURRENT_IDENTITY.set("sam")
    client("GET", "/v1/x")
    assert captured["url"].startswith("http://sam-google:8080")
    assert captured["auth"] == "Bearer sam-bridge-token"
    assert captured["identity"] == "sam"

    # Alex has no override, so he falls back to the shared bridge — correct
    # for services that genuinely are shared.
    gw._client.CURRENT_IDENTITY.set("alex")
    client("GET", "/v1/x")
    assert captured["url"].startswith("http://shared-google:8080")
    assert captured["auth"] == "Bearer shared-bridge-token"
    assert captured["identity"] == "alex"


def test_the_identity_header_reaches_the_bridge(gw, monkeypatch):
    """The bridge's authoritative gate matches identity-scoped grants against
    this header, so it must actually be sent."""
    monkeypatch.setenv("VIKUNJA_BRIDGE_TOKEN", "t")
    client = gw._client.bridge_client("VIKUNJA", "vikunja-bridge")
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured.update(request.headers)

        class R:
            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    monkeypatch.setattr(gw._client.urllib.request, "urlopen", fake_urlopen)
    gw._client.CURRENT_IDENTITY.set("alex")
    client("GET", "/v1/tasks")
    assert captured.get("X-agentbox-identity") == "alex"


def test_no_identity_sends_no_identity_header(gw, monkeypatch):
    """Single-operator must not start asserting an empty identity, which a
    bridge could mistake for a real one."""
    monkeypatch.setenv("VIKUNJA_BRIDGE_TOKEN", "t")
    client = gw._client.bridge_client("VIKUNJA", "vikunja-bridge")
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured.update(request.headers)

        class R:
            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    monkeypatch.setattr(gw._client.urllib.request, "urlopen", fake_urlopen)
    gw._client.CURRENT_IDENTITY.set("")
    client("GET", "/v1/tasks")
    assert "X-agentbox-identity" not in captured


def test_a_missing_bridge_token_refuses_rather_than_calling_anonymously(gw, monkeypatch):
    monkeypatch.delenv("VIKUNJA_BRIDGE_TOKEN", raising=False)
    monkeypatch.delenv("VIKUNJA_BRIDGE_TOKEN_ALEX", raising=False)
    client = gw._client.bridge_client("VIKUNJA", "vikunja-bridge")
    gw._client.CURRENT_IDENTITY.set("alex")
    from mcp_base import ToolError
    with pytest.raises(ToolError):
        client("GET", "/v1/tasks")


# --- credential isolation ---------------------------------------------------------


def test_the_gateway_holds_no_upstream_credentials():
    """The reason consolidating MCPs is safe while consolidating bridges is
    not. A leak here costs a scoped local token, not a handle on real mail."""
    source = code_of(APP / "server.py")
    for module in APP.glob("integrations/*.py"):
        source += module.read_text()
    for forbidden in ("REFRESH_TOKEN", "CLIENT_SECRET", "HA_TOKEN",
                      "VIKUNJA_API_TOKEN", "MEMORY_REVIEW_TOKEN"):
        assert forbidden not in source, forbidden


def test_identity_scoped_grants_only_match_their_identity():
    import time

    import policy_gate as pg

    grants = REPO / ".test-grants.json"
    consumed = REPO / ".test-consumed.json"
    try:
        grants.write_text(json.dumps({"grants": [{
            "tool": "archive_gmail", "expires_at": time.time() + 60,
            "single_use": False, "identity": "sam"}]}))
        assert pg.consume_grant("archive_gmail", grants,
                                consumed_path=consumed, identity="sam")
        assert not pg.consume_grant("archive_gmail", grants,
                                    consumed_path=consumed, identity="alex")
        # And an unscoped grant still covers anyone — the pre-identity default.
        grants.write_text(json.dumps({"grants": [{
            "tool": "archive_gmail", "expires_at": time.time() + 60,
            "single_use": False}]}))
        assert pg.consume_grant("archive_gmail", grants,
                                consumed_path=consumed, identity="alex")
        assert pg.consume_grant("archive_gmail", grants,
                                consumed_path=consumed, identity=None)
    finally:
        grants.unlink(missing_ok=True)
        consumed.unlink(missing_ok=True)


def test_the_journal_records_who_acted(tmp_path):
    """Reflection and tier arguments both need to know whose calls they are
    reading, or a two-person journal is unusable for either."""
    os.environ["MCP_OUTCOME_FILE"] = str(tmp_path / "o.jsonl")
    spec = importlib.util.spec_from_file_location(
        "ol_identity", REPO / "services" / "templates" / "mcp" / "outcome_log.py")
    log = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(log)
    log.record("agentbox-mcp", "list_tasks", log.OK, identity="sam")
    entry = json.loads((tmp_path / "o.jsonl").read_text().strip())
    assert entry["identity"] == "sam"
