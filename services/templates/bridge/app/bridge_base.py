"""Shared base for Agentbox credential bridges.

A bridge is a narrow HTTP service that holds a credential (an API token, OAuth
refresh token, etc.) and exposes a small allowlisted API. The assistant talks
to the bridge with a *bridge token*; it never sees the upstream credential.

This module bakes in the security-critical parts so individual bridges only
declare their routes:

- **Fail-closed auth**: if the bridge token is unset, every authed request is
  rejected 503 (never silently open). Comparison is constant-time.
- **JSON errors**: unexpected exceptions become a JSON 500, never a raw
  traceback on the socket.
- **Bounded bodies**: request bodies over `MAX_BODY_BYTES` are refused.
- **Unauthenticated `/health`** for liveness probes.

Copy this file verbatim into a new bridge's `app/` directory; do not edit it
per-bridge (that is how divergence bugs start). See
`skills/adding-a-bridge/SKILL.md`.
"""
from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

MAX_BODY_BYTES = int(os.environ.get("BRIDGE_MAX_BODY_BYTES", str(1 << 20)))


class BridgeError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# route key: (METHOD, path) -> handler(self, body) -> (status, payload)
Route = Callable[["BridgeHandler", dict[str, Any] | None], "tuple[int, Any]"]


class BridgeHandler(BaseHTTPRequestHandler):
    """Subclass and set `bridge_token` + `routes`. Nothing else is required.

    `bridge_token`: the expected token (usually read from env at import time).
    `routes`: dict mapping (method, path) to a handler. Handlers may raise
    BridgeError for expected failures; anything else becomes a JSON 500.
    Paths listed in `public_paths` skip auth (e.g. "/health").
    """

    server_version = "agentbox-bridge/1.0"
    bridge_token: str = ""
    routes: dict[tuple[str, str], Route] = {}
    public_paths: frozenset[str] = frozenset({"/health"})

    # --- helpers subclasses use ------------------------------------------
    def json_body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return None
        if length > MAX_BODY_BYTES:
            raise BridgeError(413, "request body too large")
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise BridgeError(400, "invalid JSON body")
        if not isinstance(body, dict):
            raise BridgeError(400, "JSON body must be an object")
        return body

    def send_json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # --- internals -------------------------------------------------------
    def _require_auth(self) -> None:
        if not self.bridge_token:
            raise BridgeError(503, "bridge token is not configured")
        provided = self.headers.get("Authorization", "")
        if not hmac.compare_digest(provided, f"Bearer {self.bridge_token}"):
            raise BridgeError(401, "invalid bridge token")

    def _dispatch(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if path == "/health" and method == "GET":
                self.send_json(200, {"ok": True, "service": self.server_version})
                return
            handler = self.routes.get((method, path))
            if handler is None:
                raise BridgeError(404, "not found")
            if path not in self.public_paths:
                self._require_auth()
            body = self.json_body() if method in ("POST", "PATCH", "PUT") else None
            status, payload = handler(self, body)
            self.send_json(status, payload)
        except BridgeError as exc:
            self.send_json(exc.status, {"error": exc.message})
        except Exception as exc:  # noqa: BLE001 — never leak a traceback
            self.send_json(500, {"error": f"internal error: {type(exc).__name__}"})

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)


def serve(handler_cls: type[BridgeHandler]) -> None:
    host = os.environ.get("BRIDGE_HOST", "0.0.0.0")
    port = int(os.environ.get("BRIDGE_PORT", "8080"))
    httpd = ThreadingHTTPServer((host, port), handler_cls)
    print(f"{handler_cls.server_version} listening on {host}:{port}", flush=True)
    httpd.serve_forever()
