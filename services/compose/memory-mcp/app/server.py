#!/usr/bin/env python3
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HOST = os.environ.get("MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("MCP_PORT", "8080"))
BRIDGE_URL = os.environ.get("MEMORY_BRIDGE_URL", "http://memory-bridge:8080").rstrip("/")
BRIDGE_TOKEN = os.environ.get("MEMORY_BRIDGE_TOKEN", "")
MCP_SHARED_TOKEN = os.environ.get("MEMORY_MCP_SHARED_TOKEN", "")
PROTOCOL_VERSION = "2025-06-18"
MAX_BODY_BYTES = 128 * 1024


class McpError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class ToolError(Exception):
    pass


def bridge_request(method, path, payload=None):
    if not BRIDGE_TOKEN:
        raise ToolError("MEMORY_BRIDGE_TOKEN is not configured")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {BRIDGE_TOKEN}", "Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{BRIDGE_URL}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        raise ToolError(f"bridge HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:500]}")
    except urllib.error.URLError as exc:
        raise ToolError(f"bridge connection failed: {exc.reason}")


def schema_object(properties, required=None):
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


MEMORY_FIELDS = {
    "type": {"type": "string", "description": "health_profile, food_preference, project_context, workflow_rule, household_preference, etc."},
    "statement": {"type": "string"},
    "source": {"type": "string"},
    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    "sensitivity": {"type": "string", "enum": ["low", "medium", "high"]},
    "metadata": {"type": "object"},
}


TOOLS = [
    {"name": "propose_memory", "description": "Create a memory proposal for user review.", "inputSchema": schema_object(MEMORY_FIELDS, ["statement"])},
    {"name": "list_memory_proposals", "description": "List pending memory proposals.", "inputSchema": schema_object({})},
    {"name": "approve_memory_proposal", "description": "Approve a memory proposal after explicit user approval.", "inputSchema": schema_object({"proposal_id": {"type": "string"}}, ["proposal_id"])},
    {"name": "write_memory", "description": "Write an approved low-risk memory. Do not use for sensitive memory without approval.", "inputSchema": schema_object(MEMORY_FIELDS, ["statement"])},
    {"name": "search_memories", "description": "List stored memories for context retrieval.", "inputSchema": schema_object({})},
]


def tool_call(name, args):
    args = args or {}
    if name == "propose_memory":
        return bridge_request("POST", "/v1/proposals", args)
    if name == "list_memory_proposals":
        return bridge_request("GET", "/v1/proposals")
    if name == "approve_memory_proposal":
        return bridge_request("POST", f"/v1/proposals/{urllib.parse.quote(args['proposal_id'])}/approve")
    if name == "write_memory":
        return bridge_request("POST", "/v1/memories", args)
    if name == "search_memories":
        return bridge_request("GET", "/v1/memories")
    raise ToolError(f"unknown tool: {name}")


def tool_result(payload, is_error=False):
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, sort_keys=True)
    return {"content": [{"type": "text", "text": text}], "structuredContent": payload if isinstance(payload, (dict, list)) else {"message": text}, "isError": bool(is_error)}


def mcp_response(message_id, result=None, error=None):
    payload = {"jsonrpc": "2.0", "id": message_id}
    if error:
        payload["error"] = error
    else:
        payload["result"] = result
    return payload


def handle_mcp(message):
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params") or {}
    if method == "initialize":
        return mcp_response(message_id, {"protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "memory-mcp", "version": "0.1.0"}, "instructions": "Use for reviewed personal memory. Propose sensitive memory; do not silently store health or high-stakes facts."})
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return mcp_response(message_id, {})
    if method == "tools/list":
        return mcp_response(message_id, {"tools": TOOLS})
    if method == "tools/call":
        try:
            return mcp_response(message_id, tool_result(tool_call(params.get("name"), params.get("arguments") or {})))
        except Exception as exc:
            return mcp_response(message_id, tool_result(str(exc), True))
    if message_id is not None:
        return mcp_response(message_id, error={"code": -32601, "message": f"method not found: {method}"})
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "memory-mcp/0.1"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {self.command} {self.path} {fmt % args}", file=sys.stderr, flush=True)

    def send_json(self, status, payload):
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_empty(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"ok": True})
            return
        self.send_empty(405)

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/mcp":
            self.send_empty(404)
            return
        try:
            if MCP_SHARED_TOKEN and self.headers.get("Authorization", "") != f"Bearer {MCP_SHARED_TOKEN}":
                raise McpError(-32001, "invalid MCP bearer token")
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY_BYTES:
                self.send_json(413, {"error": "request too large"})
                return
            response = handle_mcp(json.loads(self.rfile.read(length).decode("utf-8")))
            self.send_empty(202) if response is None else self.send_json(200, response)
        except McpError as exc:
            self.send_json(400, {"jsonrpc": "2.0", "error": {"code": exc.code, "message": exc.message}})
        except json.JSONDecodeError:
            self.send_json(400, {"jsonrpc": "2.0", "error": {"code": -32700, "message": "parse error"}})
        except Exception as exc:
            self.send_json(500, {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(exc)}})


if __name__ == "__main__":
    print(f"memory MCP listening on {HOST}:{PORT}; bridge={BRIDGE_URL}", file=sys.stderr, flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
