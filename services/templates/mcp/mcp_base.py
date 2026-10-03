"""The MCP server base that agentbox-mcp is built on.

It advertises tools, checks every call against the approval policy, and hands
allowed calls to `dispatch`, which forwards them to the bridge holding the
credential.

Auth lives here so no server writes its own. A hand-written check such as
`if MCP_SHARED_TOKEN and token != MCP_SHARED_TOKEN: reject` skips the check
when the token is unset. The rules here are:

- **Auth fails closed.** With no token configured, every /mcp request is
  refused. Tokens are compared in constant time.
- **One policy check.** Every tools/call goes through `policy_gate.check` in
  one place, so a tool cannot skip it.
- **Bounded bodies.** Requests over MAX_BODY_BYTES are refused.
- **Open /health and /ready.** They carry no data and grant nothing.
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import mcp_protocol as protocol
import outcome_log
import policy_gate
from mcp_protocol import McpError, ToolError, schema_object  # noqa: F401

MAX_BODY_BYTES = int(os.environ.get("MCP_MAX_BODY_BYTES", str(128 * 1024)))


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
    # {identity: token}. The bearer token presented is the identity, so whoever
    # holds a person's token is that person. The process serving one person's
    # conversation only has that person's token, so an instruction hidden in
    # content cannot switch to someone else. With no identities, the single
    # shared_token is used and there is no identity.
    identity_tokens: dict[str, str] = {}
    identity: str | None = None

    # --- internals -------------------------------------------------------
    def _require_origin(self) -> None:
        """Refuse an Origin header that is present but not on the list."""
        origin = self.headers.get("Origin")
        if origin is None:
            return
        if origin not in protocol.ALLOWED_ORIGINS:
            raise McpError(protocol.ERR_FORBIDDEN_ORIGIN, f"origin not allowed: {origin[:80]}")

    def _validate_arguments(self, name: str, arguments: dict) -> None:
        """Check the tool's declared `required` arguments before dispatch.

        Without this, a missing argument surfaces as `internal error:
        KeyError`, which names nothing and looks like a server fault, so a
        model retries the same broken call. Reading the schema rather than
        writing per-tool checks means a new tool cannot forget.
        """
        for tool in self.tools:
            if tool.get("name") != name:
                continue
            required = (tool.get("inputSchema") or {}).get("required") or []
            missing = [key for key in required
                       if key not in arguments
                       or arguments[key] is None
                       or arguments[key] == ""]
            if missing:
                raise ToolError(
                    f"{name} is missing required argument(s): {', '.join(missing)}")
            return
        raise ToolError(f"unknown tool: {name}")

    def _validate_headers(self, message: dict) -> None:
        """Require the routing headers to match the body.

        Current clients copy `method` and `params.name` into headers so a proxy
        can route without reading the body. If a proxy routed on the header
        while this server acted on the body, a mismatch would be an attack, so
        a mismatch is refused with -32020.

        Older clients send none of these headers and are not checked here.
        """
        method = message.get("method")
        params = message.get("params") or {}
        meta = params.get("_meta") or {}
        body_version = meta.get(protocol.META_VERSION)
        header_version = self.headers.get("MCP-Protocol-Version")

        if body_version is None:
            return  # legacy request; header rules below do not apply

        if header_version is None:
            raise McpError(protocol.ERR_HEADER_MISMATCH,
                           "MCP-Protocol-Version header is required")
        if header_version != body_version:
            raise McpError(protocol.ERR_HEADER_MISMATCH,
                           f"MCP-Protocol-Version header '{header_version}' does not "
                           f"match body value '{body_version}'")

        header_method = self.headers.get("Mcp-Method")
        if header_method is None:
            raise McpError(protocol.ERR_HEADER_MISMATCH, "Mcp-Method header is required")
        if header_method != method:
            raise McpError(protocol.ERR_HEADER_MISMATCH,
                           f"Mcp-Method header '{header_method}' does not match "
                           f"body method '{method}'")

        # Mcp-Name is required for the methods that name a target.
        name_source = {"tools/call": "name", "prompts/get": "name",
                       "resources/read": "uri"}.get(str(method))
        if name_source is None:
            return
        body_name = params.get(name_source)
        header_name = self.headers.get("Mcp-Name")
        if header_name is None:
            raise McpError(protocol.ERR_HEADER_MISMATCH,
                           f"Mcp-Name header is required for {method}")
        if protocol.decode_header_value(header_name) != body_name:
            raise McpError(protocol.ERR_HEADER_MISMATCH,
                           f"Mcp-Name header does not match body value for {method}")

    def _require_protocol_version(self) -> None:
        """Refuse an MCP-Protocol-Version header this server cannot serve.

        A missing header is fine. The specification says to assume 2025-03-26.
        """
        version = self.headers.get("MCP-Protocol-Version")
        if version is None:
            return
        if version not in protocol.SUPPORTED_PROTOCOL_VERSIONS and version != protocol.ASSUMED_PROTOCOL_VERSION:
            raise McpError(protocol.ERR_UNSUPPORTED_VERSION, "Unsupported protocol version",
                           {"supported": list(protocol.SUPPORTED_PROTOCOL_VERSIONS),
                            "requested": version[:32]})

    def _require_auth(self) -> None:
        """Fail closed. With no token configured, everything is refused rather
        than the check being skipped."""
        provided = self.headers.get("Authorization", "")
        if self.identity_tokens:
            # Each comparison is constant-time. The loop can only reveal how
            # many identities exist, which is not a secret.
            for name, token in self.identity_tokens.items():
                if token and hmac.compare_digest(provided, f"Bearer {token}"):
                    self.identity = name
                    return
            raise McpError(protocol.ERR_UNAUTHORIZED, "invalid MCP bearer token")
        if not self.shared_token:
            raise McpError(protocol.ERR_UNAUTHORIZED,
                           "MCP shared token is not configured; refusing all calls")
        if not hmac.compare_digest(provided, f"Bearer {self.shared_token}"):
            raise McpError(protocol.ERR_UNAUTHORIZED, "invalid MCP bearer token")
        self.identity = None

    def _reply(self, message_id: Any, result: Any = None, error: Any = None) -> dict:
        return protocol.response(message_id, result, error, service_name=self.service_name)

    def _capabilities(self) -> dict:
        return {"tools": {"listChanged": False}}

    def _handle(self, message: dict) -> dict | None:
        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params") or {}
        meta = params.get("_meta") or {}

        # A current client declares its version on each request. Refuse one
        # this server cannot speak rather than answer in a different version.
        requested = meta.get(protocol.META_VERSION)
        if requested is not None and requested not in protocol.SUPPORTED_PROTOCOL_VERSIONS:
            raise McpError(protocol.ERR_UNSUPPORTED_VERSION, "Unsupported protocol version",
                           {"supported": list(protocol.SUPPORTED_PROTOCOL_VERSIONS),
                            "requested": requested})

        # Required by the specification, for clients of any version.
        if method == "server/discover":
            return self._reply(message_id, {
                "supportedVersions": list(protocol.SUPPORTED_PROTOCOL_VERSIONS),
                "capabilities": self._capabilities(),
                "instructions": self.instructions,
                "ttlMs": protocol.TOOLS_TTL_MS,
                "cacheScope": protocol.CACHE_SCOPE,
            })

        if method == "initialize":
            # Only older clients send initialize. Agree on an older version
            # rather than echo whatever was asked for, because the current
            # version has no handshake.
            asked = params.get("protocolVersion")
            agreed = asked if asked in protocol.LEGACY_VERSIONS else protocol.PROTOCOL_VERSION
            return self._reply(message_id, {
                "protocolVersion": agreed,
                "capabilities": self._capabilities(),
                "serverInfo": {"name": self.service_name, "version": "1.0.0"},
                "instructions": self.instructions,
            })
        if method == "notifications/initialized":
            return None
        if method == "ping":
            # Gone from the current version, kept for older clients.
            return self._reply(message_id, {})
        if method == "tools/list":
            # Sorted, so the tool block is identical every turn and stays in
            # the model's prompt cache. These schemas are the largest fixed
            # cost per turn, and `agentbox validate` holds them to a budget.
            return self._reply(message_id, {
                "tools": sorted(self.tools, key=lambda tool: tool["name"]),
                # Let the client cache the tool block.
                "ttlMs": protocol.TOOLS_TTL_MS,
                "cacheScope": protocol.CACHE_SCOPE,
            })
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            started = time.monotonic()

            def note(outcome: str, detail: str = "", payload: Any = None) -> None:
                # Never the exception message: upstream errors quote their input.
                outcome_log.record(
                    self.service_name, str(name), outcome,
                    capability=policy_gate.capability_of(name),
                    arguments=arguments, ms=time.monotonic() - started,
                    size=len(json.dumps(payload, default=str)) if payload is not None else 0,
                    detail=detail, identity=self.identity or "")

            try:
                # Check without using up a single-use grant. The bridge checks
                # again and consumes it, and its answer is authoritative.
                policy_gate.check(name, consume=False, identity=self.identity)
                self._validate_arguments(name, arguments)
                payload = self.dispatch(name, arguments)
                note(outcome_log.OK, payload=payload)
                return self._reply(message_id, protocol.tool_result(payload))
            except policy_gate.PolicyDenied as exc:
                # Recorded because a bridge never sees these, and they show
                # what the assistant wanted and could not have.
                note(outcome_log.DENIED)
                return self._reply(message_id, protocol.tool_result(str(exc), True))
            except ToolError as exc:
                # Record a fixed reason rather than the message, which can quote
                # the input. The reason is what self-reflection can act on.
                text = str(exc)
                if "missing required argument" in text:
                    note(outcome_log.INVALID, detail="missing_required_argument")
                elif "unknown tool" in text:
                    note(outcome_log.INVALID, detail="unknown_tool")
                elif "HTTP 403" in text:
                    # A 403 from a bridge is a refusal, not a fault: an entity
                    # not on the allowlist, a path outside the repository, a
                    # protected file. Logging it as an error would make a working
                    # guardrail look like a broken tool, and self-reflection
                    # would then advise against using it.
                    note(outcome_log.DENIED, detail="upstream_refused")
                else:
                    note(outcome_log.ERROR, detail="upstream_rejected")
                return self._reply(message_id, protocol.tool_result(text, True))
            except Exception as exc:  # noqa: BLE001 (never leak a traceback)
                note(outcome_log.ERROR, detail=type(exc).__name__)
                return self._reply(message_id, protocol.tool_result(
                    f"internal error: {type(exc).__name__}", True))
        if message_id is not None:
            # The current specification answers an unknown method with 404.
            raise McpError(-32601, f"method not found: {method}")
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
            # Ready only if the bridge behind it is ready. /health does not
            # depend on the bridge, so a bridge outage cannot restart-loop this
            # server.
            try:
                with urllib.request.urlopen(f"{self.bridge_url}/ready", timeout=10) as resp:
                    self.send_json(200, {"ok": True, "bridge": json.loads(resp.read())})
            except urllib.error.HTTPError as exc:
                detail: Any = f"HTTP {exc.code}"
                try:
                    detail = json.loads(exc.read())
                except Exception:  # noqa: BLE001 (diagnostics only)
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
            # Origin first, so a rebinding attempt is refused before it can test
            # whether a token is valid.
            self._require_origin()
            self._require_protocol_version()
            self._require_auth()
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY_BYTES:
                self.send_json(413, {"error": "request too large"})
                return
            message = json.loads(self.rfile.read(length).decode("utf-8"))
            self._validate_headers(message)
            result = self._handle(message)
            self.send_empty(202) if result is None else self.send_json(200, result)
        except McpError as exc:
            status = {protocol.ERR_UNAUTHORIZED: 401, protocol.ERR_FORBIDDEN_ORIGIN: 403,
                      protocol.ERR_UNSUPPORTED_VERSION: 400, protocol.ERR_HEADER_MISMATCH: 400,
                      -32601: 404}.get(exc.code, 400)
            body: dict[str, Any] = {"code": exc.code, "message": exc.message}
            if exc.data is not None:
                body["data"] = exc.data
            elif exc.code == protocol.ERR_UNSUPPORTED_VERSION:
                body["data"] = {"supported": list(protocol.SUPPORTED_PROTOCOL_VERSIONS)}
            self.send_json(status, {"jsonrpc": "2.0", "error": body})
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
    if handler_cls.identity_tokens:
        empty = [n for n, t in handler_cls.identity_tokens.items() if not t]
        if empty:
            print(f"WARNING {handler_cls.service_name}: identities with empty "
                  f"tokens are unusable: {', '.join(empty)}",
                  file=sys.stderr, flush=True)
        print(f"{handler_cls.service_name}: "
              f"{len(handler_cls.identity_tokens)} identit"
              f"{'y' if len(handler_cls.identity_tokens) == 1 else 'ies'} configured",
              file=sys.stderr, flush=True)
    elif not handler_cls.shared_token:
        print(f"WARNING {handler_cls.service_name}: MCP shared token unset, "
              f"every /mcp request will be refused", file=sys.stderr, flush=True)
    print(f"{handler_cls.service_name} listening on {host}:{port}; "
          f"bridge={handler_cls.bridge_url}", file=sys.stderr, flush=True)
    ThreadingHTTPServer((host, port), handler_cls).serve_forever()
