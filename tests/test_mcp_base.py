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
