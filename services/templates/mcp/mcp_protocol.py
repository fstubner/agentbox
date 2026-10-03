"""MCP protocol details shared by the server: versions, error codes, cache
hints, and the result and error shapes."""
from __future__ import annotations

import base64
import json
import os
from typing import Any

# Current clients declare their version in each request's `_meta` and need no
# handshake. Older clients open with `initialize`. The specification allows one
# endpoint to serve both, which keeps this current without breaking the gateway,
# whose MCP client stops at 2025-11-25.
MODERN_VERSION = "2026-07-28"
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18")
SUPPORTED_PROTOCOL_VERSIONS = (MODERN_VERSION,) + LEGACY_VERSIONS
# The answer to an `initialize` asking for a version not listed above. Current
# clients never call initialize.
PROTOCOL_VERSION = LEGACY_VERSIONS[0]

# _meta keys that carry the version and client and server info per request.
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

# The specification reserves -32020 to -32099, and these three live there.
ERR_HEADER_MISMATCH = -32020
ERR_MISSING_CAPABILITY = -32021
ERR_UNSUPPORTED_VERSION = -32022
ERR_UNAUTHORIZED = -32001
ERR_FORBIDDEN_ORIGIN = -32003

# tools/list can be cached by the client. ttlMs says for how long, and
# cacheScope says whether a shared intermediary may hold it. This list belongs to
# one household, so it is private.
TOOLS_TTL_MS = int(os.environ.get("MCP_TOOLS_TTL_MS", str(3_600_000)))
CACHE_SCOPE = "private"

# JSON Schema 2020-12, declared so a client does not have to infer it.
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

# The specification requires checking Origin against DNS rebinding, where a web
# page on this machine points an attacker's domain at 127.0.0.1 and talks to a
# local server as if it were the same origin. The bearer token already stops
# that, and this is a second layer.
#
# Clients that are not browsers send no Origin, so a missing header is allowed
# and only an unlisted one is refused. The gateway sends none.
ALLOWED_ORIGINS = frozenset(
    o.strip() for o in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip())

# What the specification says to assume when a client sends no
# MCP-Protocol-Version header.
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
    """Serialise compactly. Pretty-printing added about 17% to every result and
    gives a model nothing."""
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
        # "complete" means this is the answer rather than a request for more
        # input. Older clients treat a missing field as "complete", so it is
        # always safe to send.
        result.setdefault("resultType", "complete")
        if service_name:
            meta = result.setdefault("_meta", {})
            meta.setdefault(META_SERVER_INFO, {"name": service_name, "version": "1.0.0"})
    payload["result"] = result
    return payload


def schema_object(properties: dict, required: list | None = None) -> dict:
    return {"$schema": SCHEMA_DIALECT, "type": "object", "properties": properties,
            "required": required or [], "additionalProperties": False}
