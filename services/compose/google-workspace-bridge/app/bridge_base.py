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
- **Unauthenticated `/ready`** for readiness, including upstream reachability.
- **Structured request logs** on stdout, one JSON object per request.

Copy this file verbatim into a new bridge's `app/` directory; do not edit it
per-bridge (that is how divergence bugs start). See
`skills/adding-a-bridge/SKILL.md`.

## /health vs /ready

`/health` is liveness: is this process answering? It never touches the
upstream. The container healthcheck uses it, and it must stay that way — if
liveness depended on the upstream, a Vikunja outage would mark every bridge
unhealthy and Docker would restart-loop processes that were working fine.

`/ready` is readiness: can this bridge actually do its job right now? It calls
`upstream_status()`, so it does reach the backing service. `cli/agentbox
doctor` probes it. This split exists because a Vikunja outage on 2026-07-31
went unnoticed: all six bridges reported healthy while the task backend behind
them was gone.

## Request logging

One JSON line per request on stdout, for answering "where do the tokens
actually go" from real traffic. Deliberately conservative about content:

- Never logs the Authorization header, request bodies, or response bodies.
- Logs only allowlisted query parameters (`LOGGED_QUERY_PARAMS`). Free-text
  params like a search string can carry personal data, so they are excluded by
  default rather than opted out one at a time.
- `/health` and `/ready` are suppressed unless `BRIDGE_LOG_PROBES=1`; probes
  fire every 30s per bridge and would otherwise bury real traffic.
"""
from __future__ import annotations

import hmac
import json
import os
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

MAX_BODY_BYTES = int(os.environ.get("BRIDGE_MAX_BODY_BYTES", str(1 << 20)))
LOG_PROBES = os.environ.get("BRIDGE_LOG_PROBES", "0") == "1"

# Allowlist, not a denylist: anything not named here is never logged.
LOGGED_QUERY_PARAMS = ("view", "page", "per_page", "expand")

# --- projection pushdown (docs/context-economy.md §1) ------------------------
#
# Shared so every bridge narrows results the same way. Named views rather than
# a caller-supplied field list: the agent spends one token and needs no schema
# knowledge, and /schema advertises what each view contains.
#
# `lean` means fewer FIELDS on the same items. Two neighbouring names are
# reserved for different contracts and must not be used for this:
#   `summary` — counts + top-N, i.e. fewer ITEMS
#   `compact` — a reduced view PLUS a raw-reference handle to rematerialise
VIEWS = ("full", "lean")


def resolve_view(value: str | None, allowed: tuple[str, ...] = VIEWS,
                 default: str = "full") -> str:
    """Validate a requested view. Rejects unknown values rather than silently
    falling back, so a typo cannot quietly return more data than intended."""
    view = value or default
    if view not in allowed:
        raise BridgeError(400, f"view must be one of: {', '.join(sorted(allowed))}")
    return view


def resolve_limit(value: Any, default: int, maximum: int) -> int:
    """Resolve a caller-supplied result limit.

    Every list endpoint takes one. An unbounded list is a context-economy
    problem before it is a performance problem: the caller cannot know how much
    of its window a call will consume, and the largest payload in this platform
    was found exactly this way.
    """
    if value is None or value == "":
        return default
    try:
        limit = int(value)
    except (TypeError, ValueError):
        raise BridgeError(400, "limit must be an integer")
    if limit < 1:
        raise BridgeError(400, "limit must be positive")
    return min(limit, maximum)


def project_fields(items: Any, fields: tuple[str, ...]) -> Any:
    """Narrow a list of objects to `fields`.

    Absent keys are omitted rather than nulled, so the projection never invents
    data. Non-list payloads pass through untouched, so an upstream error body
    is never reshaped into a list.
    """
    if not isinstance(items, list):
        return items
    return [{k: item[k] for k in fields if k in item}
            for item in items if isinstance(item, dict)]


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
    public_paths: frozenset[str] = frozenset({"/health", "/ready"})

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
        self._response_bytes = len(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def note(self, key: str, value: Any) -> None:
        """Attach a field to this request's log line.

        Query parameters are picked up automatically, but POST-body endpoints
        (the Google routes) would otherwise be unobservable — bodies are never
        logged. Handlers call this so the resolved view still shows up.
        """
        if not hasattr(self, "_log_extra"):
            self._log_extra = {}
        self._log_extra[key] = value

    def upstream_status(self) -> dict[str, Any]:
        """Report whether the backing service is reachable.

        Override in bridges that front a service. Must return a dict with an
        `ok` key; anything else is passed through to /ready for diagnostics.
        The default reports no upstream, which is correct for bridges that only
        call a remote API with a stored credential.
        """
        return {"ok": True, "upstream": None}

    # --- internals -------------------------------------------------------
    def _require_auth(self) -> None:
        if not self.bridge_token:
            raise BridgeError(503, "bridge token is not configured")
        provided = self.headers.get("Authorization", "")
        if not hmac.compare_digest(provided, f"Bearer {self.bridge_token}"):
            raise BridgeError(401, "invalid bridge token")

    def route_fallback(self, method: str, path: str, body: dict[str, Any] | None) -> "tuple[int, Any]":
        """Override for dynamic paths (e.g. /v1/things/{id}/action).

        Called only AFTER auth has passed — never before.
        """
        raise BridgeError(404, "not found")

    def _dispatch(self, method: str) -> None:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        started = time.monotonic()
        self._response_bytes = 0
        self._log_extra = {}
        status = 500
        try:
            if path == "/health" and method == "GET":
                status = 200
                self.send_json(status, {"ok": True, "service": self.server_version})
                return
            if path == "/ready" and method == "GET":
                upstream = self.upstream_status()
                status = 200 if upstream.get("ok") else 503
                self.send_json(status, {"service": self.server_version, **upstream})
                return
            if path not in self.public_paths:
                self._require_auth()
            body = self.json_body() if method in ("POST", "PATCH", "PUT") else None
            handler = self.routes.get((method, path))
            if handler is None:
                status, payload = self.route_fallback(method, path, body)
            else:
                status, payload = handler(self, body)
            self.send_json(status, payload)
        except BridgeError as exc:
            status = exc.status
            self.send_json(status, {"error": exc.message})
        except Exception as exc:  # noqa: BLE001 — never leak a traceback
            status = 500
            self.send_json(status, {"error": f"internal error: {type(exc).__name__}"})
        finally:
            self._log_request(method, path, status, time.monotonic() - started)

    def _log_request(self, method: str, path: str, status: int, elapsed: float) -> None:
        if path in ("/health", "/ready") and not LOG_PROBES:
            return
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        params = {k: query[k][0] for k in LOGGED_QUERY_PARAMS if query.get(k)}
        params.update(getattr(self, "_log_extra", {}))
        record = {
            "service": self.server_version.split("/")[0],
            "method": method,
            "path": path,
            "status": status,
            "bytes": getattr(self, "_response_bytes", 0),
            "ms": round(elapsed * 1000, 1),
        }
        if params:
            record["params"] = params
        try:
            print(json.dumps(record), flush=True)
        except Exception:  # noqa: BLE001 — logging must never break a response
            pass

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        """Suppressed: _log_request emits a structured line instead."""

    def log_message(self, fmt: str, *args: Any) -> None:
        # Still used by BaseHTTPRequestHandler for protocol-level errors
        # (malformed request lines etc.), which never reach _dispatch.
        print(f"{self.address_string()} - {fmt % args}", flush=True)


def serve(handler_cls: type[BridgeHandler]) -> None:
    host = os.environ.get("BRIDGE_HOST", "0.0.0.0")
    port = int(os.environ.get("BRIDGE_PORT", "8080"))
    httpd = ThreadingHTTPServer((host, port), handler_cls)
    print(f"{handler_cls.server_version} listening on {host}:{port}", flush=True)
    httpd.serve_forever()
