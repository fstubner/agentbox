"""Tests for the agentbox role router.

Runs the router against stub upstream llama-server processes (plain
http.server instances) so no models are needed.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "router"))


class StubUpstream(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible stub. Echoes the last user message."""

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size))
        last_user = next(m["content"] for m in reversed(body["messages"]) if m["role"] == "user")
        reply = {
            "choices": [{"message": {"role": "assistant", "content": f"echo:{body['model']}:{last_user[-20:]}"}}],
        }
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        data = json.dumps({"data": [{"id": "stub-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def stack():
    """Start one stub upstream and the router wired to it for all roles."""
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), StubUpstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    up_base = f"http://127.0.0.1:{upstream.server_address[1]}/v1"

    os.environ.update(
        AGENTBOX_MAIN_BASE=up_base,
        AGENTBOX_CONTEXT_BASE=up_base,
        AGENTBOX_REASON_BASE=up_base,
        AGENTBOX_LLAMA_KEY_FILE="/nonexistent",
        AGENTBOX_ROUTER_TIMEOUT_SECONDS="10",
    )
    import agentbox_router

    importlib.reload(agentbox_router)

    router = ThreadingHTTPServer(("127.0.0.1", 0), agentbox_router.Handler)
    threading.Thread(target=router.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{router.server_address[1]}"
    yield base, agentbox_router
    router.shutdown()
    upstream.shutdown()


def _post(base, path, payload):
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


def test_health(stack):
    base, _ = stack
    with urllib.request.urlopen(base + "/health", timeout=10) as resp:
        body = json.loads(resp.read())
    assert resp.status == 200
    assert body["ok"] is True
    assert set(body["checks"]) == {"main", "context", "reason"}


@pytest.mark.parametrize("path", ["/context/extract", "/reason/check", "/decide/orchestrate"])
def test_role_endpoints_route_to_upstream(stack, path):
    base, _ = stack
    status, body = _post(base, path, {"text": "check this reasoning for me"})
    assert status == 200
    assert body["text"].startswith("echo:")
    assert "elapsed_seconds" in body


def test_unknown_route_404(stack):
    base, _ = stack
    try:
        _post(base, "/nope", {"text": "x"})
        raise AssertionError("expected 404")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
        assert "routes" in json.loads(exc.read())


def test_missing_text_field_is_400(stack):
    base, _ = stack
    try:
        _post(base, "/context/extract", {"wrong_key": 1})
        raise AssertionError("expected 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400


def test_strip_thinking_variants(stack):
    _, mod = stack
    assert mod.strip_thinking("<think>hmm</think>final") == "final"
    assert mod.strip_thinking("plain answer") == "plain answer"
    # unclosed think tag: keep last paragraph
    assert mod.strip_thinking("<think>reasoning...\n\nconclusion here") == "conclusion here"


def test_final_text_empty_choices(stack):
    _, mod = stack
    assert mod.final_text({"choices": []}) == ""
    assert mod.final_text({}) == ""


def test_each_role_forwards_the_instruction(stack, monkeypatch):
    """The seam the harness escalation depends on.

    The harness sends its schema demand in `instruction` and escalates from the
    context worker to the main model. `decide_orchestrate` silently dropped the
    field, so the escalation target never saw the contract and every retry
    failed validation. Assert all three roles actually forward it — a stub that
    echoes the last 20 characters would not have caught this.
    """
    _, mod = stack
    captured = {}
    monkeypatch.setattr(mod, "chat",
                        lambda base, model, messages, **k:
                        captured.setdefault("user", messages[-1]["content"]))
    marker = "RETURN-ONLY-JSON-abc123"
    for handler in (mod.context_extract, mod.reason_check, mod.decide_orchestrate):
        captured.clear()
        handler({"text": "the email body", "instruction": marker})
        assert marker in captured["user"], handler.__name__
