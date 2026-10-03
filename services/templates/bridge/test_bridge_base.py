"""Tests for the shared bridge base: fail-closed auth, error handling, routing.

They cover the defect the base class exists to prevent, a bridge accepting an
empty bearer token when its token is unset.
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

BASE = Path(__file__).resolve().parent / "app" / "bridge_base.py"
spec = importlib.util.spec_from_file_location("bridge_base", BASE)
bridge_base = importlib.util.module_from_spec(spec)
sys.modules["bridge_base"] = bridge_base
spec.loader.exec_module(bridge_base)


def make_handler(token: str):
    def ok(handler, body):
        return 200, {"got": (body or {}).get("v")}

    return type("H", (bridge_base.BridgeHandler,), {
        "bridge_token": token,
        "routes": {("POST", "/v1/thing"): ok},
    })


def serve(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def call(base, path, token=None, body=None, method="POST"):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def test_unset_token_fails_closed():
    """An empty token must not authenticate. The bridge answers 503."""
    server, base = serve(make_handler(""))
    try:
        call(base, "/v1/thing", token="", body={"v": 1})
        raise AssertionError("expected rejection")
    except urllib.error.HTTPError as exc:
        assert exc.code == 503
    finally:
        server.shutdown()


def test_wrong_token_401():
    server, base = serve(make_handler("secret"))
    try:
        call(base, "/v1/thing", token="nope", body={"v": 1})
        raise AssertionError("expected rejection")
    except urllib.error.HTTPError as exc:
        assert exc.code == 401
    finally:
        server.shutdown()


def test_correct_token_200():
    server, base = serve(make_handler("secret"))
    try:
        status, payload = call(base, "/v1/thing", token="secret", body={"v": 42})
        assert status == 200 and payload["got"] == 42
    finally:
        server.shutdown()


def test_health_is_public():
    server, base = serve(make_handler("secret"))
    try:
        with urllib.request.urlopen(base + "/health", timeout=5) as resp:
            assert resp.status == 200 and json.loads(resp.read())["ok"] is True
    finally:
        server.shutdown()


def test_unknown_route_404():
    server, base = serve(make_handler("secret"))
    try:
        call(base, "/v1/missing", token="secret", body={})
        raise AssertionError("expected 404")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    finally:
        server.shutdown()


def test_unexpected_exception_becomes_json_500():
    def boom(handler, body):
        raise RuntimeError("kaboom")

    cls = type("H", (bridge_base.BridgeHandler,), {
        "bridge_token": "secret",
        "routes": {("POST", "/v1/boom"): boom},
    })
    server, base = serve(cls)
    try:
        call(base, "/v1/boom", token="secret", body={})
        raise AssertionError("expected 500")
    except urllib.error.HTTPError as exc:
        assert exc.code == 500
        assert "traceback" not in exc.read().decode().lower()
    finally:
        server.shutdown()


# --- readiness and structured logging ---------------------------------------


def test_ready_is_public_and_defaults_ok():
    server, base = serve(make_handler("secret"))
    try:
        with urllib.request.urlopen(base + "/ready", timeout=5) as resp:
            payload = json.loads(resp.read())
        assert resp.status == 200 and payload["ok"] is True
    finally:
        server.shutdown()


def test_ready_returns_503_when_upstream_down():
    """The Vikunja-outage case: bridge alive, backing service gone."""
    cls = type("H", (bridge_base.BridgeHandler,), {
        "bridge_token": "secret",
        "routes": {},
        "upstream_status": lambda self: {"ok": False, "upstream": {"reachable": False}},
    })
    server, base = serve(cls)
    try:
        urllib.request.urlopen(base + "/ready", timeout=5)
        raise AssertionError("expected 503")
    except urllib.error.HTTPError as exc:
        assert exc.code == 503
    finally:
        server.shutdown()


def test_health_stays_up_when_upstream_is_down():
    """Liveness must not depend on the upstream, or Docker restart-loops us."""
    cls = type("H", (bridge_base.BridgeHandler,), {
        "bridge_token": "secret",
        "routes": {},
        "upstream_status": lambda self: {"ok": False, "upstream": {"reachable": False}},
    })
    server, base = serve(cls)
    try:
        with urllib.request.urlopen(base + "/health", timeout=5) as resp:
            assert resp.status == 200 and json.loads(resp.read())["ok"] is True
    finally:
        server.shutdown()


def test_request_log_is_json_with_size_and_allowlisted_params(capfd):
    server, base = serve(make_handler("secret"))
    try:
        call(base, "/v1/thing?view=lean&s=secret-search", token="secret",
             body={"v": 1}, method="POST")
    finally:
        server.shutdown()
    lines = [ln for ln in capfd.readouterr().out.splitlines() if ln.startswith("{")]
    record = json.loads(lines[-1])
    assert record["method"] == "POST"
    assert record["path"] == "/v1/thing"
    assert record["status"] == 200
    assert record["bytes"] > 0
    assert isinstance(record["ms"], float)
    assert record["params"] == {"view": "lean"}


def test_request_log_never_contains_credentials_or_free_text(capfd):
    """Auth header, body, and non-allowlisted params must never be logged."""
    server, base = serve(make_handler("secret"))
    try:
        call(base, "/v1/thing?s=private-search-term", token="secret",
             body={"v": "sensitive-body-value"}, method="POST")
    finally:
        server.shutdown()
    out = capfd.readouterr().out
    assert "secret" not in out
    assert "private-search-term" not in out
    assert "sensitive-body-value" not in out


def test_probe_requests_are_not_logged(capfd):
    server, base = serve(make_handler("secret"))
    try:
        urllib.request.urlopen(base + "/health", timeout=5).read()
        urllib.request.urlopen(base + "/ready", timeout=5).read()
    finally:
        server.shutdown()
    assert [ln for ln in capfd.readouterr().out.splitlines() if ln.startswith("{")] == []


# --- shared projection helpers ----------------------------------------------


def test_resolve_view_defaults_to_full():
    assert bridge_base.resolve_view(None) == "full"
    assert bridge_base.resolve_view("") == "full"


def test_resolve_view_accepts_lean():
    assert bridge_base.resolve_view("lean") == "lean"


def test_resolve_view_rejects_unknown():
    try:
        bridge_base.resolve_view("compact")
        raise AssertionError("expected rejection")
    except bridge_base.BridgeError as exc:
        assert exc.status == 400


def test_project_fields_omits_absent_keys():
    assert bridge_base.project_fields([{"a": 1}], ("a", "b")) == [{"a": 1}]


def test_project_fields_passes_through_non_lists():
    assert bridge_base.project_fields(None, ("a",)) is None
    assert bridge_base.project_fields({"error": "x"}, ("a",)) == {"error": "x"}


def test_project_fields_skips_non_dict_entries():
    assert bridge_base.project_fields([{"a": 1}, "junk", None], ("a",)) == [{"a": 1}]


def test_note_appears_in_the_request_log(capfd):
    """POST-body endpoints record their view via note(); bodies are never logged."""
    def noted(handler, body):
        handler.note("view", "lean")
        return 200, {"ok": True}

    cls = type("H", (bridge_base.BridgeHandler,), {
        "bridge_token": "secret",
        "routes": {("POST", "/v1/noted"): noted},
    })
    server, base = serve(cls)
    try:
        call(base, "/v1/noted", token="secret", body={})
    finally:
        server.shutdown()
    lines = [ln for ln in capfd.readouterr().out.splitlines() if ln.startswith("{")]
    assert json.loads(lines[-1])["params"] == {"view": "lean"}


# --- persisted request log --------------------------------------------------


def test_request_log_is_written_to_the_configured_file(tmp_path, monkeypatch):
    """stdout is not a record: docker logs do not survive a container recreate,
    and every deploy recreates."""
    log = tmp_path / "requests.jsonl"
    monkeypatch.setattr(bridge_base, "LOG_FILE", str(log))
    server, base = serve(make_handler("secret"))
    try:
        call(base, "/v1/thing?view=lean", token="secret", body={"v": 1})
    finally:
        server.shutdown()
    lines = [ln for ln in log.read_text().splitlines() if ln.startswith("{")]
    record = json.loads(lines[-1])
    assert record["path"] == "/v1/thing"
    assert record["params"] == {"view": "lean"}


def test_persisted_log_rotates_at_the_size_bound(tmp_path, monkeypatch):
    log = tmp_path / "requests.jsonl"
    log.write_text("x" * 200)
    monkeypatch.setattr(bridge_base, "LOG_FILE", str(log))
    monkeypatch.setattr(bridge_base, "LOG_MAX_BYTES", 100)
    server, base = serve(make_handler("secret"))
    try:
        call(base, "/v1/thing", token="secret", body={"v": 1})
    finally:
        server.shutdown()
    assert log.with_suffix(".jsonl.1").exists(), "previous generation kept"
    assert log.read_text().startswith("{"), "current file restarted"


def test_unwritable_log_file_does_not_break_the_response(tmp_path, monkeypatch):
    """A failure to write the log must not fail the request."""
    monkeypatch.setattr(bridge_base, "LOG_FILE", str(tmp_path / "nope" / "x.jsonl"))
    server, base = serve(make_handler("secret"))
    try:
        status, payload = call(base, "/v1/thing", token="secret", body={"v": 7})
        assert status == 200 and payload["got"] == 7
    finally:
        server.shutdown()


def test_no_log_file_configured_is_fine(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_base, "LOG_FILE", "")
    server, base = serve(make_handler("secret"))
    try:
        status, _ = call(base, "/v1/thing", token="secret", body={"v": 1})
        assert status == 200
    finally:
        server.shutdown()
