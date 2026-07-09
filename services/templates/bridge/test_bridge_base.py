"""Tests for the shared bridge base: fail-closed auth, error handling, routing.

These guard the exact defect that motivated the base class: a bridge accepting
an empty bearer token when its token is unset.
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
    """The motivating bug: empty token must NOT authenticate — expect 503."""
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
