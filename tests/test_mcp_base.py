"""Tests for the shared MCP base — specifically that auth fails closed.

The three MCPs were written separately and their auth diverged. Each carried:

    if MCP_SHARED_TOKEN and provided != MCP_SHARED_TOKEN: reject

With the token unset — which it was, on all three — the condition
short-circuits and no check runs. An unauthenticated request from the host
reached Gmail, because the MCP holds the bridge token.

The bridges had a shared base and a regression test for exactly this shape.
The MCPs did not. These are that test.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MCP_APP = REPO / "services" / "templates" / "mcp"
sys.path.insert(0, str(MCP_APP))

# policy_gate binds its paths as default arguments at import, so the real policy
# has to be in place before mcp_base imports it. Without this every tool looks
# unmapped and the gate denies it — correct behaviour, wrong thing to test here.
import os  # noqa: E402
os.environ["AGENTBOX_RUNTIME_POLICY"] = str(REPO / "policies" / "approval-policy.yaml")
os.environ["AGENTBOX_POLICY_GRANTS"] = str(REPO / ".no-such-grants.json")
spec = importlib.util.spec_from_file_location("mcp_base", MCP_APP / "mcp_base.py")
mb = importlib.util.module_from_spec(spec)
sys.modules["mcp_base"] = mb
spec.loader.exec_module(mb)


def make(token):
    return type("H", (mb.McpHandler,), {
        "service_name": "test-mcp",
        "tools": [{"name": "list_tasks", "description": "x", "inputSchema": {}}],
        "dispatch": staticmethod(lambda name, args: {"ok": name}),
        "shared_token": token,
    })


def serve(cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def rpc(base, method, params=None, token=None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params or {}}).encode()
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + "/mcp", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def test_unset_token_refuses_everything():
    """The motivating bug: an unset token must NOT disable the check."""
    server, base = serve(make(""))
    try:
        rpc(base, "tools/list")
        raise AssertionError("expected rejection")
    except urllib.error.HTTPError as exc:
        assert exc.code == 401
    finally:
        server.shutdown()


def test_no_credential_is_refused():
    server, base = serve(make("secret"))
    try:
        rpc(base, "tools/list")
        raise AssertionError("expected rejection")
    except urllib.error.HTTPError as exc:
        assert exc.code == 401
    finally:
        server.shutdown()


def test_wrong_credential_is_refused():
    server, base = serve(make("secret"))
    try:
        rpc(base, "tools/list", token="wrong")
        raise AssertionError("expected rejection")
    except urllib.error.HTTPError as exc:
        assert exc.code == 401
    finally:
        server.shutdown()


def test_correct_credential_is_accepted():
    server, base = serve(make("secret"))
    try:
        assert rpc(base, "tools/list", token="secret")["result"]["tools"][0]["name"] == "list_tasks"
    finally:
        server.shutdown()


def test_tool_call_passes_the_policy_gate():
    """An unmapped tool must be refused even with a valid credential."""
    cls = type("H", (mb.McpHandler,), {
        "service_name": "test-mcp",
        "tools": [{"name": "not_a_real_tool", "description": "x", "inputSchema": {}}],
        "dispatch": staticmethod(lambda name, args: {"ran": True}),
        "shared_token": "secret",
    })
    server, base = serve(cls)
    try:
        result = rpc(base, "tools/call", {"name": "not_a_real_tool"}, token="secret")["result"]
        assert result["isError"] is True
        assert "approval" in result["content"][0]["text"]
    finally:
        server.shutdown()


def test_health_needs_no_credential():
    server, base = serve(make("secret"))
    try:
        with urllib.request.urlopen(base + "/health", timeout=5) as resp:
            assert resp.status == 200 and json.loads(resp.read())["ok"] is True
    finally:
        server.shutdown()


def test_unexpected_error_does_not_leak_a_traceback():
    def boom(name, args):
        raise RuntimeError("kaboom with a secret in it")

    cls = type("H", (mb.McpHandler,), {
        "service_name": "test-mcp", "tools": [], "shared_token": "secret",
        "dispatch": staticmethod(boom),
    })
    server, base = serve(cls)
    try:
        result = rpc(base, "tools/call", {"name": "list_tasks"}, token="secret")["result"]
        assert result["isError"] is True
        assert "kaboom" not in result["content"][0]["text"]
    finally:
        server.shutdown()


def test_every_mcp_reads_its_token_from_env_not_hardcoded():
    for mcp in ("vikunja-mcp", "memory-mcp", "google-workspace-mcp-lite"):
        src = (REPO / "services" / "compose" / mcp / "app" / "server.py").read_text()
        assert "shared_token = os.environ.get(" in src, mcp


# --- protocol conformance ---------------------------------------------------


def test_negotiates_rather_than_echoing_the_requested_version():
    """Echoing claims support for anything the client asks for, including
    revisions that changed the wire format underneath us."""
    server, base = serve(make("secret"))
    try:
        agreed = rpc(base, "initialize", {"protocolVersion": "2099-01-01"},
                     token="secret")["result"]["protocolVersion"]
        assert agreed == mb.PROTOCOL_VERSION
    finally:
        server.shutdown()


def test_accepts_a_version_we_actually_support():
    """initialize negotiates within the legacy set only — the modern revision
    has no handshake, so offering it here would be incoherent."""
    server, base = serve(make("secret"))
    try:
        for version in mb.LEGACY_VERSIONS:
            agreed = rpc(base, "initialize", {"protocolVersion": version},
                         token="secret")["result"]["protocolVersion"]
            assert agreed == version
    finally:
        server.shutdown()


def test_tools_are_returned_in_deterministic_order():
    """Stable ordering lets a client cache the tool list and improves prompt
    cache hits — the schemas are the largest fixed per-turn cost."""
    cls = type("H", (mb.McpHandler,), {
        "service_name": "test-mcp", "shared_token": "secret",
        "tools": [{"name": n, "description": "x", "inputSchema": {}}
                  for n in ("zebra", "alpha", "middle")],
        "dispatch": staticmethod(lambda name, args: {}),
    })
    server, base = serve(cls)
    try:
        names = [t["name"] for t in rpc(base, "tools/list", token="secret")["result"]["tools"]]
        assert names == ["alpha", "middle", "zebra"]
    finally:
        server.shutdown()


def test_schemas_declare_the_json_schema_dialect():
    schema = mb.schema_object({"x": {"type": "string"}}, ["x"])
    assert schema["$schema"] == mb.SCHEMA_DIALECT


def test_tool_failures_are_tool_errors_not_protocol_errors(monkeypatch):
    """2025-11-25 (SEP-1303): input validation failures must come back as tool
    execution errors so the model can self-correct, not as JSON-RPC errors."""
    def bad(name, args):
        raise mb.ToolError("message_id is required")

    # This test is about the shape of a tool failure, not the gate. policy_gate
    # binds its paths as default arguments at import, so which policy file it
    # sees depends on test module import order — pin the gate open here and let
    # test_policy_gate own that behaviour.
    monkeypatch.setattr(mb.policy_gate, "check", lambda *a, **k: None)

    cls = type("H", (mb.McpHandler,), {
        "service_name": "test-mcp", "shared_token": "secret",
        "tools": [{"name": "list_tasks", "description": "x", "inputSchema": {}}],
        "dispatch": staticmethod(bad),
    })
    server, base = serve(cls)
    try:
        payload = rpc(base, "tools/call", {"name": "list_tasks"}, token="secret")
        assert "error" not in payload, "must not be a protocol-level error"
        assert payload["result"]["isError"] is True
        assert "message_id is required" in payload["result"]["content"][0]["text"]
    finally:
        server.shutdown()


# --- Streamable HTTP transport MUSTs ----------------------------------------


def test_request_without_origin_is_allowed():
    """Non-browser clients send no Origin. Hermes is one."""
    server, base = serve(make("secret"))
    try:
        assert rpc(base, "tools/list", token="secret")["result"]
    finally:
        server.shutdown()


def test_unrecognised_origin_is_refused_with_403():
    """Spec MUST: validate Origin to prevent DNS rebinding. A page that
    resolves an attacker domain to 127.0.0.1 must not reach this server."""
    server, base = serve(make("secret"))
    try:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                           "params": {}}).encode()
        req = urllib.request.Request(base + "/mcp", data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer secret",
            "Origin": "https://evil.example",
        })
        urllib.request.urlopen(req, timeout=5)
        raise AssertionError("expected rejection")
    except urllib.error.HTTPError as exc:
        assert exc.code == 403
    finally:
        server.shutdown()


def test_origin_is_checked_before_the_token():
    """A rebinding attempt should be refused before it learns whether a
    credential was valid."""
    server, base = serve(make("secret"))
    try:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                           "params": {}}).encode()
        req = urllib.request.Request(base + "/mcp", data=body, method="POST", headers={
            "Content-Type": "application/json", "Origin": "https://evil.example",
        })
        urllib.request.urlopen(req, timeout=5)
        raise AssertionError("expected rejection")
    except urllib.error.HTTPError as exc:
        assert exc.code == 403, "403 for origin, not 401 for the missing token"
    finally:
        server.shutdown()


def test_unsupported_protocol_version_header_is_400():
    """Spec MUST: reject an unsupported MCP-Protocol-Version with 400."""
    server, base = serve(make("secret"))
    try:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                           "params": {}}).encode()
        req = urllib.request.Request(base + "/mcp", data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer secret",
            "MCP-Protocol-Version": "1999-01-01",
        })
        urllib.request.urlopen(req, timeout=5)
        raise AssertionError("expected rejection")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
    finally:
        server.shutdown()


def test_supported_protocol_version_header_is_accepted():
    server, base = serve(make("secret"))
    try:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                           "params": {}}).encode()
        req = urllib.request.Request(base + "/mcp", data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer secret",
            "MCP-Protocol-Version": mb.PROTOCOL_VERSION,
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
    finally:
        server.shutdown()


def test_get_on_the_mcp_endpoint_returns_405():
    """We offer no SSE stream. The spec allows 405 to say so explicitly."""
    server, base = serve(make("secret"))
    try:
        urllib.request.urlopen(base + "/mcp", timeout=5)
        raise AssertionError("expected 405")
    except urllib.error.HTTPError as exc:
        assert exc.code == 405
    finally:
        server.shutdown()


# --- dual-era: 2026-07-28 alongside the legacy handshake --------------------


MODERN_META = {"_meta": {mb.META_VERSION: mb.MODERN_VERSION,
                         mb.META_CLIENT_INFO: {"name": "test", "version": "1"}}}


def test_server_discover_is_implemented():
    """A MUST at 2026-07-28. Clients use it to learn versions up front."""
    server, base = serve(make("secret"))
    try:
        result = rpc(base, "server/discover", dict(MODERN_META), token="secret")["result"]
        assert mb.MODERN_VERSION in result["supportedVersions"]
        assert "tools" in result["capabilities"]
        assert result["resultType"] == "complete"
        assert result["_meta"][mb.META_SERVER_INFO]["name"] == "test-mcp"
    finally:
        server.shutdown()


def test_modern_request_needs_no_handshake():
    """Stateless: a version in _meta is enough, no initialize first."""
    server, base = serve(make("secret"))
    try:
        result = rpc(base, "tools/list", dict(MODERN_META), token="secret")["result"]
        assert result["tools"][0]["name"] == "list_tasks"
    finally:
        server.shutdown()


def test_unsupported_version_in_meta_returns_the_modern_error():
    """UnsupportedProtocolVersionError, listing what we do support, so the
    client can retry rather than guess."""
    server, base = serve(make("secret"))
    try:
        payload = rpc(base, "tools/list",
                      {"_meta": {mb.META_VERSION: "1900-01-01"}}, token="secret")
        assert payload["error"]["code"] == mb.ERR_UNSUPPORTED_VERSION
        assert "1900-01-01" == payload["error"]["data"]["requested"]
        assert mb.MODERN_VERSION in payload["error"]["data"]["supported"]
    finally:
        server.shutdown()


def test_legacy_initialize_still_works():
    """The whole point of dual-era: Hermes tops out at 2025-11-25 today."""
    server, base = serve(make("secret"))
    try:
        result = rpc(base, "initialize", {"protocolVersion": "2025-11-25"},
                     token="secret")["result"]
        assert result["protocolVersion"] == "2025-11-25"
    finally:
        server.shutdown()


def test_initialize_never_answers_with_a_handshakeless_version():
    """2026-07-28 has no handshake. Answering initialize with it would tell a
    legacy client to speak a dialect whose semantics it just used wrongly."""
    server, base = serve(make("secret"))
    try:
        result = rpc(base, "initialize", {"protocolVersion": "1999-01-01"},
                     token="secret")["result"]
        assert result["protocolVersion"] in mb.LEGACY_VERSIONS
        assert result["protocolVersion"] != mb.MODERN_VERSION
    finally:
        server.shutdown()


def test_every_result_carries_result_type():
    server, base = serve(make("secret"))
    try:
        for method, params in (("tools/list", {}), ("ping", {}),
                               ("server/discover", {})):
            assert rpc(base, method, params, token="secret")["result"]["resultType"] == "complete"
    finally:
        server.shutdown()


def test_tools_list_is_cacheable():
    """CacheableResult: the tool block is the largest fixed part of the prompt,
    and this lets a client hold it instead of refetching."""
    server, base = serve(make("secret"))
    try:
        result = rpc(base, "tools/list", token="secret")["result"]
        assert result["ttlMs"] > 0
        assert result["cacheScope"] == "private"
    finally:
        server.shutdown()
