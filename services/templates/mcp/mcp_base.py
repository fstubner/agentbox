"""Shared base for Agentbox MCP services.

An MCP service is the assistant-facing surface: it advertises tools, gates each
call against the approval policy, and forwards the allowed ones to a bridge
that holds the actual credential.

This exists because the three MCPs were written separately and their auth
diverged. The bridges have had a shared base with a fail-closed regression test
since a real bypass shipped; the MCPs each got hand-written auth, and one of
them read:

    if MCP_SHARED_TOKEN and request_token != MCP_SHARED_TOKEN: reject

With the token unset — which it was, on all three — that condition
short-circuits and no check runs. An unauthenticated request from the host
reached straight through to Gmail, because the MCP holds the bridge token. The
same class of bypass as the bridge one, in the layer nobody had written the
test for.

The fix is structural rather than a patch: auth lives here, fails closed, and a
new MCP inherits it instead of reimplementing it.

Security properties baked in:

- **Fail-closed auth**: an unset shared token rejects every /mcp request. Never
  silently open. Constant-time comparison.
- **Policy gate**: every tools/call passes `policy_gate.check` at one choke
  point, so a tool cannot skip it by omission.
- **Bounded bodies**: requests over MAX_BODY_BYTES are refused.
- **Unauthenticated /health and /ready**: liveness and transitive readiness,
  which carry no data and no capability.

Copy this file verbatim into a new MCP's app/ directory alongside
policy_gate.py and approval-policy.yaml; do not edit it per-service.
`cli/agentbox validate` fails on drift in any of the three.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import policy_gate

PROTOCOL_VERSION = "2025-06-18"
MAX_BODY_BYTES = int(os.environ.get("MCP_MAX_BODY_BYTES", str(128 * 1024)))


class McpError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ToolError(Exception):
    """An expected tool failure. Returned to the model, never a traceback."""


def tool_result(payload: Any, is_error: bool = False) -> dict[str, Any]:
    """Serialise compactly. indent=2 inflated every result by ~17% and buys a
    model nothing."""
    text = payload if isinstance(payload, str) else json.dumps(
        payload, sort_keys=True, separators=(",", ":"))
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload if isinstance(payload, (dict, list)) else {"message": text},
        "isError": bool(is_error),
    }


def response(message_id: Any, result: Any = None, error: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": message_id}
    if error:
        payload["error"] = error
    else:
        payload["result"] = result
    return payload


def schema_object(properties: dict, required: list | None = None) -> dict:
    return {"type": "object", "properties": properties,
            "required": required or [], "additionalProperties": False}


class McpHandler(BaseHTTPRequestHandler):
    """Subclass and set `service_name`, `tools`, `dispatch`, `bridge_url`,
    `shared_token`, and optionally `instructions`.

    `dispatch(name, args)` runs the tool. It is called only after the policy
    gate has allowed it, so it never needs to check permissions itself.
    """

    service_name: str = "agentbox-mcp"
    instructions: str = ""
    tools: list[dict] = []
    dispatch: Callable[[str, dict], Any] = staticmethod(lambda name, args: None)
    bridge_url: str = ""
    shared_token: str = ""

    # --- internals -------------------------------------------------------
    def _require_auth(self) -> None:
        """Fail closed. An unset token rejects everything rather than
        disabling the check — the defect this base class exists to prevent."""
        if not self.shared_token:
            raise McpError(-32001, "MCP shared token is not configured; refusing all calls")
        provided = self.headers.get("Authorization", "")
        if not hmac.compare_digest(provided, f"Bearer {self.shared_token}"):
            raise McpError(-32001, "invalid MCP bearer token")

    def _handle(self, message: dict) -> dict | None:
        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            return response(message_id, {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.service_name, "version": "1.0.0"},
                "instructions": self.instructions,
            })
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return response(message_id, {})
        if method == "tools/list":
            return response(message_id, {"tools": self.tools})
        if method == "tools/call":
            name = params.get("name")
            try:
                # Non-consuming: deny early with a good message, but leave the
                # single-use grant for the bridge, whose answer is authoritative.
                policy_gate.check(name, consume=False)
                return response(message_id, tool_result(self.dispatch(name, params.get("arguments") or {})))
            except policy_gate.PolicyDenied as exc:
                return response(message_id, tool_result(str(exc), True))
            except ToolError as exc:
                return response(message_id, tool_result(str(exc), True))
            except Exception as exc:  # noqa: BLE001 — never leak a traceback
                return response(message_id, tool_result(
                    f"internal error: {type(exc).__name__}", True))
        if message_id is not None:
            return response(message_id, error={"code": -32601,
                                               "message": f"method not found: {method}"})
        return None

    def send_json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(200, {"ok": True, "service": self.service_name})
            return
        if self.path == "/ready":
            # Transitive: this MCP is only useful if the bridge it fronts can
            # reach its own upstream. /health stays liveness-only so a bridge
            # outage cannot restart-loop a working MCP.
            try:
                with urllib.request.urlopen(f"{self.bridge_url}/ready", timeout=10) as resp:
                    self.send_json(200, {"ok": True, "bridge": json.loads(resp.read())})
            except urllib.error.HTTPError as exc:
                detail: Any = f"HTTP {exc.code}"
                try:
                    detail = json.loads(exc.read())
                except Exception:  # noqa: BLE001 — diagnostics only
                    pass
                self.send_json(503, {"ok": False, "bridge": detail})
            except Exception as exc:  # noqa: BLE001
                self.send_json(503, {"ok": False, "bridge": f"unreachable: {type(exc).__name__}"})
            return
        self.send_empty(405)

    def do_POST(self) -> None:
        if urllib.parse.urlparse(self.path).path != "/mcp":
            self.send_empty(404)
            return
        try:
            self._require_auth()
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY_BYTES:
                self.send_json(413, {"error": "request too large"})
                return
            result = self._handle(json.loads(self.rfile.read(length).decode("utf-8")))
            self.send_empty(202) if result is None else self.send_json(200, result)
        except McpError as exc:
            self.send_json(401 if exc.code == -32001 else 400,
                           {"jsonrpc": "2.0", "error": {"code": exc.code, "message": exc.message}})
        except json.JSONDecodeError:
            self.send_json(400, {"jsonrpc": "2.0",
                                 "error": {"code": -32700, "message": "parse error"}})
        except Exception as exc:  # noqa: BLE001
            self.send_json(500, {"jsonrpc": "2.0",
                                 "error": {"code": -32603, "message": type(exc).__name__}})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} {self.command} {self.path} {fmt % args}",
              file=sys.stderr, flush=True)


def serve(handler_cls: type[McpHandler]) -> None:
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8080"))
    if not handler_cls.shared_token:
        print(f"WARNING {handler_cls.service_name}: MCP shared token unset — "
              f"every /mcp request will be refused", file=sys.stderr, flush=True)
    print(f"{handler_cls.service_name} listening on {host}:{port}; "
          f"bridge={handler_cls.bridge_url}", file=sys.stderr, flush=True)
    ThreadingHTTPServer((host, port), handler_cls).serve_forever()
