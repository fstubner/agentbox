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

import base64
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import outcome_log
import policy_gate

# Dual-era, as the specification names it: modern clients declare their version
# in per-request `_meta` and need no handshake; legacy clients open with
# `initialize` and get session semantics. A server MAY serve both on the same
# endpoint, which is how we can be current without breaking the only client we
# have — Hermes ships mcp 1.28.1, whose ceiling is 2025-11-25.
MODERN_VERSION = "2026-07-28"
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18")
SUPPORTED_PROTOCOL_VERSIONS = (MODERN_VERSION,) + LEGACY_VERSIONS
# What we answer a legacy `initialize` with when the client asks for something
# we do not know. A modern client never calls initialize.
PROTOCOL_VERSION = LEGACY_VERSIONS[0]

# _meta keys carrying per-request identity and version at 2026-07-28.
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

# Error codes from the 2026-07-28 allocation policy: -32020..-32099 is reserved
# for the specification, and these three were renumbered into it.
ERR_HEADER_MISMATCH = -32020
ERR_MISSING_CAPABILITY = -32021
ERR_UNSUPPORTED_VERSION = -32022
ERR_UNAUTHORIZED = -32001
ERR_FORBIDDEN_ORIGIN = -32003

# tools/list is a CacheableResult at 2026-07-28: ttlMs is a freshness hint so a
# client can cache the tool block instead of refetching it, and cacheScope says
# whether a shared intermediary may hold it. Ours is per-operator, so private.
TOOLS_TTL_MS = int(os.environ.get("MCP_TOOLS_TTL_MS", str(3_600_000)))
CACHE_SCOPE = "private"

# JSON Schema 2020-12 is the default dialect as of 2025-11-25 (SEP-1613).
# Declared explicitly so a client need not infer it.
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
MAX_BODY_BYTES = int(os.environ.get("MCP_MAX_BODY_BYTES", str(128 * 1024)))

# Streamable HTTP requires Origin validation: "Servers MUST validate the Origin
# header on all incoming connections to prevent DNS rebinding attacks."
#
# The attack: a page in a browser on this host resolves an attacker domain to
# 127.0.0.1 and then talks to a local MCP server as if it were same-origin.
# Auth blunts it — the page has no bearer token — but the specification names
# Origin as the control, and defence in depth is the point.
#
# A non-browser client sends no Origin at all, which is why absent is allowed
# and present-but-unlisted is refused. Hermes sends none.
ALLOWED_ORIGINS = frozenset(
    o.strip() for o in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip())

# Assumed when a client sends no MCP-Protocol-Version header, per the spec's
# backwards-compatibility rule.
ASSUMED_PROTOCOL_VERSION = "2025-03-26"


class McpError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def decode_header_value(value: str) -> str:
    """Undo the Base64 sentinel a client uses for values that are not header-safe.

    Format is `=?base64?<b64>?=`. Servers MUST decode before comparing to the
    body, or a name with a space in it looks like a mismatch.
    """
    if value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2]).decode("utf-8")
        except Exception:  # noqa: BLE001 — malformed encoding is a mismatch
            return value
    return value


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


def response(message_id: Any, result: Any = None, error: Any = None,
             service_name: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": message_id}
    if error:
        payload["error"] = error
        return payload
    if isinstance(result, dict):
        # Every result carries resultType at 2026-07-28; "complete" means this
        # is the answer rather than a request for more input. Clients on earlier
        # revisions must treat a missing field as "complete", so sending it
        # always is safe and saves branching on era.
        result.setdefault("resultType", "complete")
        if service_name:
            meta = result.setdefault("_meta", {})
            meta.setdefault(META_SERVER_INFO, {"name": service_name, "version": "1.0.0"})
    payload["result"] = result
    return payload


def schema_object(properties: dict, required: list | None = None) -> dict:
    return {"$schema": SCHEMA_DIALECT, "type": "object", "properties": properties,
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
    def _require_origin(self) -> None:
        """403 on a present-but-unrecognised Origin (spec MUST)."""
        origin = self.headers.get("Origin")
        if origin is None:
            return
        if origin not in ALLOWED_ORIGINS:
            raise McpError(ERR_FORBIDDEN_ORIGIN, f"origin not allowed: {origin[:80]}")

    def _validate_arguments(self, name: str, arguments: dict) -> None:
        """Enforce the tool's own declared `required` list before dispatch.

        Every tool publishes an inputSchema saying which arguments are
        required, and nothing checked it: a model that omitted one reached the
        handler, hit `args["project_id"]`, and got back `internal error:
        KeyError`. That is unactionable — it does not name the argument, and it
        reads as a server fault rather than a malformed call, so the model's
        reasonable next move is to retry the same broken call.

        Driven by the schema rather than per-tool code so a new tool cannot
        forget to do it.
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
        """Header/body agreement, required at 2026-07-28.

        The transport mirrors `method` and `params.name` into headers so
        intermediaries can route without parsing the body. If a load balancer
        routes on the header while we execute on the body, the two disagree and
        that gap is the vulnerability. So: they must match, or -32020.

        Only enforced for modern requests. A legacy client sends none of these
        and is served by the handshake instead — that is what dual-era means.
        """
        method = message.get("method")
        params = message.get("params") or {}
        meta = params.get("_meta") or {}
        body_version = meta.get(META_VERSION)
        header_version = self.headers.get("MCP-Protocol-Version")

        if body_version is None:
            return  # legacy request; header rules below do not apply

        if header_version is None:
            raise McpError(ERR_HEADER_MISMATCH,
                           "MCP-Protocol-Version header is required")
        if header_version != body_version:
            raise McpError(ERR_HEADER_MISMATCH,
                           f"MCP-Protocol-Version header '{header_version}' does not "
                           f"match body value '{body_version}'")

        header_method = self.headers.get("Mcp-Method")
        if header_method is None:
            raise McpError(ERR_HEADER_MISMATCH, "Mcp-Method header is required")
        if header_method != method:
            raise McpError(ERR_HEADER_MISMATCH,
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
            raise McpError(ERR_HEADER_MISMATCH,
                           f"Mcp-Name header is required for {method}")
        if decode_header_value(header_name) != body_name:
            raise McpError(ERR_HEADER_MISMATCH,
                           f"Mcp-Name header does not match body value for {method}")

    def _require_protocol_version(self) -> None:
        """Reject an unsupported MCP-Protocol-Version header (spec MUST).

        Absent is not an error — the spec says assume 2025-03-26, which we can
        still serve.
        """
        version = self.headers.get("MCP-Protocol-Version")
        if version is None:
            return
        if version not in SUPPORTED_PROTOCOL_VERSIONS and version != ASSUMED_PROTOCOL_VERSION:
            raise McpError(ERR_UNSUPPORTED_VERSION, "Unsupported protocol version",
                           {"supported": list(SUPPORTED_PROTOCOL_VERSIONS),
                            "requested": version[:32]})

    def _require_auth(self) -> None:
        """Fail closed. An unset token rejects everything rather than
        disabling the check — the defect this base class exists to prevent."""
        if not self.shared_token:
            raise McpError(ERR_UNAUTHORIZED,
                           "MCP shared token is not configured; refusing all calls")
        provided = self.headers.get("Authorization", "")
        if not hmac.compare_digest(provided, f"Bearer {self.shared_token}"):
            raise McpError(ERR_UNAUTHORIZED, "invalid MCP bearer token")

    def _reply(self, message_id: Any, result: Any = None, error: Any = None) -> dict:
        return response(message_id, result, error, service_name=self.service_name)

    def _capabilities(self) -> dict:
        return {"tools": {"listChanged": False}}

    def _handle(self, message: dict) -> dict | None:
        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params") or {}
        meta = params.get("_meta") or {}

        # Modern era: the client declares its version per request and expects no
        # handshake. Reject a version we cannot serve rather than answering in
        # a dialect the client did not ask for.
        requested = meta.get(META_VERSION)
        if requested is not None and requested not in SUPPORTED_PROTOCOL_VERSIONS:
            raise McpError(ERR_UNSUPPORTED_VERSION, "Unsupported protocol version",
                           {"supported": list(SUPPORTED_PROTOCOL_VERSIONS),
                            "requested": requested})

        # MUST be implemented, and answerable by either era.
        if method == "server/discover":
            return self._reply(message_id, {
                "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
                "capabilities": self._capabilities(),
                "instructions": self.instructions,
                "ttlMs": TOOLS_TTL_MS,
                "cacheScope": CACHE_SCOPE,
            })

        if method == "initialize":
            # Negotiate rather than echo. Echoing the client's version claims
            # support for anything it asks for, including revisions that changed
            # the wire format underneath us.
            # Legacy only: a modern client never sends initialize. Negotiate
            # within the legacy set so we never answer the handshake with a
            # version whose semantics have no handshake.
            asked = params.get("protocolVersion")
            agreed = asked if asked in LEGACY_VERSIONS else PROTOCOL_VERSION
            return self._reply(message_id, {
                "protocolVersion": agreed,
                "capabilities": self._capabilities(),
                "serverInfo": {"name": self.service_name, "version": "1.0.0"},
                "instructions": self.instructions,
            })
        if method == "notifications/initialized":
            return None
        if method == "ping":
            # Removed at 2026-07-28, kept for legacy clients that still send it.
            return self._reply(message_id, {})
        if method == "tools/list":
            # Deterministic order. 2026-07-28 makes this a SHOULD explicitly for
            # client-side caching and LLM prompt-cache hit rates; it is harmless
            # and beneficial at any version, and the tool schemas are the single
            # largest fixed cost in this system at ~2,250 tokens per turn.
            return self._reply(message_id, {
                "tools": sorted(self.tools, key=lambda tool: tool["name"]),
                # CacheableResult: let the client hold the tool block rather
                # than refetch it. Private because this list is per-operator.
                "ttlMs": TOOLS_TTL_MS,
                "cacheScope": CACHE_SCOPE,
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
                    detail=detail)

            try:
                # Non-consuming: deny early with a good message, but leave the
                # single-use grant for the bridge, whose answer is authoritative.
                policy_gate.check(name, consume=False)
                self._validate_arguments(name, arguments)
                payload = self.dispatch(name, arguments)
                note(outcome_log.OK, payload=payload)
                return self._reply(message_id, tool_result(payload))
            except policy_gate.PolicyDenied as exc:
                # The most interesting record in the file: the assistant wanted
                # something it could not have. Bridges never see these at all.
                note(outcome_log.DENIED)
                return self._reply(message_id, tool_result(str(exc), True))
            except ToolError as exc:
                # A fixed reason, not the exception class: every failure here is
                # a ToolError, so `detail: "ToolError"` told a reflecting
                # assistant only that something went wrong — which it already
                # knew from the outcome. The reason is the actionable part, and
                # these strings are fixed rather than derived from the message,
                # which would quote the input.
                text = str(exc)
                if "missing required argument" in text:
                    note(outcome_log.INVALID, detail="missing_required_argument")
                elif "unknown tool" in text:
                    note(outcome_log.INVALID, detail="unknown_tool")
                else:
                    note(outcome_log.ERROR, detail="upstream_rejected")
                return self._reply(message_id, tool_result(text, True))
            except Exception as exc:  # noqa: BLE001 — never leak a traceback
                note(outcome_log.ERROR, detail=type(exc).__name__)
                return self._reply(message_id, tool_result(
                    f"internal error: {type(exc).__name__}", True))
        if message_id is not None:
            # 404 for an unimplemented method at 2026-07-28, which is how a
            # client tells a modern server apart from a legacy 404.
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
            # Origin first: a rebinding attempt should be refused before it can
            # probe whether a token is valid.
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
            status = {ERR_UNAUTHORIZED: 401, ERR_FORBIDDEN_ORIGIN: 403,
                      ERR_UNSUPPORTED_VERSION: 400, ERR_HEADER_MISMATCH: 400,
                      -32601: 404}.get(exc.code, 400)
            body: dict[str, Any] = {"code": exc.code, "message": exc.message}
            if exc.data is not None:
                body["data"] = exc.data
            elif exc.code == ERR_UNSUPPORTED_VERSION:
                body["data"] = {"supported": list(SUPPORTED_PROTOCOL_VERSIONS)}
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
    if not handler_cls.shared_token:
        print(f"WARNING {handler_cls.service_name}: MCP shared token unset — "
              f"every /mcp request will be refused", file=sys.stderr, flush=True)
    print(f"{handler_cls.service_name} listening on {host}:{port}; "
          f"bridge={handler_cls.bridge_url}", file=sys.stderr, flush=True)
    ThreadingHTTPServer((host, port), handler_cls).serve_forever()
