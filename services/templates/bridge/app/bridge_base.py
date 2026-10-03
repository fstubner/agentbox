"""The base every credential bridge is built on.

A bridge is a small HTTP service that holds one credential, such as an API
token or an OAuth refresh token, and exposes a narrow API in front of it.
Callers present a bridge token and never see the upstream credential.

Each bridge only declares its routes. This module supplies the rest.

- **Auth fails closed.** With no bridge token configured, every authenticated
  request gets a 503. Tokens are compared in constant time.
- **JSON errors.** An unexpected exception becomes a JSON 500, never a
  traceback on the socket.
- **Bounded bodies.** Bodies over `MAX_BODY_BYTES` are refused.
- **Open `/health` and `/ready`** for liveness and readiness.
- **Request logs**, one JSON object per request.

Every bridge image copies this file from services/templates at build time, so
there is one copy. See `skills/adding-a-bridge/SKILL.md`.

## /health and /ready

`/health` says whether the process answers, and never touches the upstream.
The container healthcheck uses it. If it depended on the upstream, a Vikunja
outage would mark every bridge unhealthy and Docker would restart-loop
processes that were working fine.

`/ready` says whether the bridge can do its job. It calls `upstream_status()`,
which does reach the backing service, and `cli/agentbox doctor` checks it.
Without it, a stopped Vikunja looks healthy everywhere.

## Request logging

One JSON line per request, to show where the bytes go in real traffic. It
leaves out content as follows.

- The Authorization header and request and response bodies are never logged.
- Only allowlisted query parameters are logged (`LOGGED_QUERY_PARAMS`). A
  free-text parameter such as a search string can carry personal data.
- `/health` and `/ready` are left out unless `BRIDGE_LOG_PROBES=1`, because
  probes every 30 seconds would bury real traffic.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import time
import traceback
import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MAX_BODY_BYTES = int(os.environ.get("BRIDGE_MAX_BODY_BYTES", str(1 << 20)))
LOG_PROBES = os.environ.get("BRIDGE_LOG_PROBES", "0") == "1"
# The request log on disk. stdout is for tailing. This file survives the
# container being recreated, which every deploy does.
LOG_FILE = os.environ.get("BRIDGE_LOG_FILE", "")
LOG_MAX_BYTES = int(os.environ.get("BRIDGE_LOG_MAX_BYTES", str(32 << 20)))

# An allowlist. Anything not named here is never logged.
LOGGED_QUERY_PARAMS = ("view", "page", "per_page", "expand")

# --- projection pushdown -----------------------------------------------------
#
# Shared so every bridge narrows results the same way. Callers pick a named
# view rather than a list of fields, which costs the model one token and no
# knowledge of the schema. /schema says what each view contains.
#
# `lean` means fewer fields on the same items. Two other names are reserved
# for different meanings and must not be used for this.
#   `summary` means counts and the top few, so fewer items.
#   `compact` means a reduced view plus a handle to fetch the full one.
VIEWS = ("full", "lean")


def resolve_view(value: str | None, allowed: tuple[str, ...] = VIEWS,
                 default: str = "full") -> str:
    """Validate a requested view. An unknown value is refused rather than
    ignored, so a typo cannot return more data than intended."""
    view = value or default
    if view not in allowed:
        raise BridgeError(400, f"view must be one of: {', '.join(sorted(allowed))}")
    return view


def resolve_limit(value: Any, default: int, maximum: int) -> int:
    """Resolve a caller-supplied result limit.

    Every list endpoint takes one. Without a limit, a caller cannot know how
    much of the model's context a call will use.
    """
    if value is None or value == "":
        return default
    try:
        limit = int(value)
    except (TypeError, ValueError):
        raise BridgeError(400, "limit must be an integer") from None
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


# --- policy enforcement -----------------------------------------------------
#
# agentbox-mcp already checks every tool call, but it also holds this bridge's
# token. If the check and the token lived only in that one process, compromising
# it would defeat both. So the bridge checks again, against the same policy and
# grants, and its check is authoritative. The idea comes from OpenShell,
# which enforces egress outside the agent's sandbox.
#
# A bridge declares which capability a request uses. agentbox-mcp checks
# without using up a grant, for a quick refusal with a clear message, and the
# bridge uses it up.

try:
    import policy_gate
except ImportError:  # a bridge with no policy file mounted enforces nothing
    policy_gate = None  # type: ignore[assignment]


class BridgeError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# Routes map (METHOD, path) to handler(self, body), which returns
# (status, payload).
Route = Callable[["BridgeHandler", dict[str, Any] | None], "tuple[int, Any]"]


class BridgeHandler(BaseHTTPRequestHandler):
    """Subclass and set `bridge_token` and `routes`. Nothing else is required.

    `bridge_token` is the expected token, usually read from the environment.
    `routes` maps (method, path) to a handler. A handler raises BridgeError for
    an expected failure, and anything else becomes a JSON 500. Paths in
    `public_paths`, such as "/health", skip auth.
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
            raise BridgeError(400, "invalid JSON body") from None
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

        Query parameters are logged automatically, but bodies never are, so a
        POST endpoint such as the Google routes calls this to log its view.
        """
        if not hasattr(self, "_log_extra"):
            self._log_extra = {}
        self._log_extra[key] = value

    def upstream_status(self) -> dict[str, Any]:
        """Report whether the backing service is reachable.

        Override in a bridge that fronts a service. Return a dict with an `ok`
        key, and anything else in it is passed through to /ready. The default
        reports no upstream, which suits a bridge that only calls a remote API.
        """
        return {"ok": True, "upstream": None}

    # --- internals -------------------------------------------------------
    def _require_auth(self) -> None:
        if not self.bridge_token:
            raise BridgeError(503, "bridge token is not configured")
        provided = self.headers.get("Authorization", "")
        if not hmac.compare_digest(provided, f"Bearer {self.bridge_token}"):
            raise BridgeError(401, "invalid bridge token")

    def capability_for(self, method: str, path: str,
                       body: dict[str, Any] | None) -> str | None:
        """The policy capability this request uses, or None if it needs none.

        Override in a bridge that exposes anything gated. It sees the body,
        because some routes, such as gmail/modify, carry their verb there.
        """
        return None

    def _enforce_policy(self, method: str, path: str, body: dict[str, Any] | None) -> None:
        capability = self.capability_for(method, path, body)
        if capability is None:
            return
        if policy_gate is None:
            raise BridgeError(503, "policy enforcement unavailable; refusing a gated request")
        # agentbox-mcp says who it is acting for. This is trusted because only
        # agentbox-mcp holds the bridge token. Scoped grants match against it,
        # and without it only unscoped grants apply.
        identity = self.headers.get("X-Agentbox-Identity", "").strip() or None
        try:
            policy_gate.check_capability(capability, subject=path,
                                         identity=identity)
        except policy_gate.PolicyDenied as exc:
            raise BridgeError(403, str(exc)) from None

    def route_fallback(self, method: str, path: str, body: dict[str, Any] | None) -> tuple[int, Any]:
        """Override for dynamic paths such as /v1/things/{id}/action.

        Only called after auth has passed.
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
            self._enforce_policy(method, path, body)
            handler = self.routes.get((method, path))
            if handler is None:
                status, payload = self.route_fallback(method, path, body)
            else:
                status, payload = handler(self, body)
            self.send_json(status, payload)
        except BridgeError as exc:
            status = exc.status
            self.send_json(status, {"error": exc.message})
        except Exception as exc:  # noqa: BLE001 (never leak a traceback)
            status = 500
            # The caller gets only the class name, because an exception message
            # often quotes the input that caused it. The full traceback goes to
            # stderr, which ends up in `docker logs`, where the operator can
            # read it and the assistant cannot.
            traceback.print_exc(file=sys.stderr)
            self.send_json(status, {"error": f"internal error: {type(exc).__name__}"})
        finally:
            self._log_request(method, path, status, time.monotonic() - started)

    def _write_log_file(self, line: str) -> None:
        """Append the record to the mounted log, if one is configured.

        `docker logs` does not survive a deploy, so this file is the record.
        It rotates by size and keeps one previous file, uncompressed. It is for
        measuring traffic, not auditing, and a log that can fill the disk would
        be worse than one that loses old lines.
        """
        if not LOG_FILE:
            return
        try:
            path = Path(LOG_FILE)
            if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
                path.replace(path.with_suffix(path.suffix + ".1"))
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass  # logging must never break a response

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
            line = json.dumps(record)
            print(line, flush=True)
            self._write_log_file(line)
        except Exception:  # noqa: BLE001 (logging must never break a response)
            pass

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        """Silenced, because _log_request writes a structured line instead."""

    def log_message(self, fmt: str, *args: Any) -> None:
        # BaseHTTPRequestHandler still uses this for protocol errors, such as
        # a malformed request line, which never reach _dispatch.
        print(f"{self.address_string()} - {fmt % args}", flush=True)


def serve(handler_cls: type[BridgeHandler]) -> None:
    host = os.environ.get("BRIDGE_HOST", "0.0.0.0")
    port = int(os.environ.get("BRIDGE_PORT", "8080"))
    httpd = ThreadingHTTPServer((host, port), handler_cls)
    print(f"{handler_cls.server_version} listening on {host}:{port}", flush=True)
    httpd.serve_forever()
