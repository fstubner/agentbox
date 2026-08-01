"""Tests for the memory review gate (architecture extension point #2).

The invariant: the assistant may PROPOSE a memory; it may not approve one and
it may not write straight to durable memory. Those require an operator
credential the assistant does not hold.

Before this gate existed the memory MCP exposed both approve_memory_proposal
and write_memory, so the assistant could accept its own proposals or skip the
queue entirely. The only thing standing in the way was a sentence in the tool
description — "after explicit user approval" — which is documentation, not
enforcement. These tests exist so that cannot silently come back.
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

import pytest

REPO = Path(__file__).resolve().parent.parent
BASE_APP = REPO / "services" / "templates" / "bridge" / "app"
BRIDGE = REPO / "services" / "compose" / "memory-bridge" / "app" / "bridge.py"
MCP = REPO / "services" / "compose" / "memory-mcp" / "app" / "server.py"

sys.path.insert(0, str(BASE_APP))


def load_bridge(tmp_path, review_token):
    """Import memory-bridge with a temp store and a chosen review token."""
    import os
    os.environ["MEMORY_PATH"] = str(tmp_path / "memory.json")
    os.environ["MEMORY_BRIDGE_TOKEN"] = "bridge-secret"
    os.environ["MEMORY_REVIEW_TOKEN"] = review_token
    spec = importlib.util.spec_from_file_location(f"mem_{review_token or 'none'}", BRIDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def serve(module):
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.MemoryBridge)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def call(base, path, method="POST", body=None, review=None):
    headers = {"Authorization": "Bearer bridge-secret", "Content-Type": "application/json"}
    if review is not None:
        headers["X-Memory-Review-Token"] = review
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


@pytest.fixture
def gated(tmp_path):
    module = load_bridge(tmp_path, "operator-secret")
    server, base = serve(module)
    yield module, base
    server.shutdown()


def propose(base, statement="likes tea"):
    _, item = call(base, "/v1/proposals", body={"statement": statement})
    return item["id"]


def test_proposing_needs_no_review_token(gated):
    """The assistant must still be able to propose freely."""
    _, base = gated
    status, item = call(base, "/v1/proposals", body={"statement": "likes tea"})
    assert status == 201 and item["status"] == "proposed"


def test_approval_without_review_token_is_forbidden(gated):
    _, base = gated
    pid = propose(base)
    with pytest.raises(urllib.error.HTTPError) as exc:
        call(base, f"/v1/proposals/{pid}/approve")
    assert exc.value.code == 403


def test_approval_with_wrong_review_token_is_forbidden(gated):
    _, base = gated
    pid = propose(base)
    with pytest.raises(urllib.error.HTTPError) as exc:
        call(base, f"/v1/proposals/{pid}/approve", review="guessed")
    assert exc.value.code == 403


def test_direct_write_to_memory_is_forbidden(gated):
    """The bypass path matters as much as the approve path — a queue you can
    skip is not a queue."""
    _, base = gated
    with pytest.raises(urllib.error.HTTPError) as exc:
        call(base, "/v1/memories", body={"statement": "sneaky"})
    assert exc.value.code == 403


def test_operator_can_approve(gated):
    _, base = gated
    pid = propose(base)
    status, item = call(base, f"/v1/proposals/{pid}/approve", review="operator-secret")
    assert status == 200 and item["status"] == "approved"
    _, memories = call(base, "/v1/memories", method="GET")
    assert [m["id"] for m in memories["memories"]] == [pid]


def test_operator_can_reject_and_queue_shrinks(gated):
    _, base = gated
    pid = propose(base)
    status, item = call(base, f"/v1/proposals/{pid}/reject",
                        body={"reason": "not durable"}, review="operator-secret")
    assert status == 200 and item["status"] == "rejected"
    _, pending = call(base, "/v1/proposals", method="GET")
    assert pending["proposals"] == []
    _, memories = call(base, "/v1/memories", method="GET")
    assert memories["memories"] == []


def test_unset_review_token_fails_closed(tmp_path):
    """With no operator token configured, approval is impossible — durable
    memory stops accepting writes rather than accepting them from anyone."""
    module = load_bridge(tmp_path, "")
    server, base = serve(module)
    try:
        pid = propose(base)
        with pytest.raises(urllib.error.HTTPError) as exc:
            call(base, f"/v1/proposals/{pid}/approve", review="anything")
        assert exc.value.code == 503
    finally:
        server.shutdown()


def test_mcp_does_not_expose_approval_or_direct_write():
    """The assistant-facing surface must not carry the operator tools."""
    source = MCP.read_text(encoding="utf-8")
    tool_names = [line for line in source.splitlines() if '"name":' in line]
    joined = "\n".join(tool_names)
    assert "approve_memory_proposal" not in joined
    assert "write_memory" not in joined
    assert "propose_memory" in joined
