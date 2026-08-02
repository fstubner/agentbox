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
BRIDGE_URL = os.environ.get("GOOGLE_BRIDGE_URL", "http://google-workspace-bridge:8080").rstrip("/")
BRIDGE_TOKEN = os.environ.get("GOOGLE_BRIDGE_TOKEN", "")
MCP_SHARED_TOKEN = os.environ.get("GOOGLE_MCP_SHARED_TOKEN", "")
PROTOCOL_VERSION = "2025-06-18"
MAX_BODY_BYTES = 128 * 1024


class McpError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class ToolError(Exception):
    pass


def bridge_post(path, payload=None):
    if not BRIDGE_TOKEN:
        raise ToolError("GOOGLE_BRIDGE_TOKEN is not configured")
    data = json.dumps(payload or {}).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {BRIDGE_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(f"{BRIDGE_URL}{path}", data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        raise ToolError(f"bridge HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:500]}")
    except urllib.error.URLError as exc:
        raise ToolError(f"bridge connection failed: {exc.reason}")


def schema_object(properties, required=None):
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


TOOLS = [
    {"name": "search_gmail", "description": "Search Gmail messages.", "inputSchema": schema_object({"query": {"type": "string"}, "max_results": {"type": "integer", "minimum": 1, "maximum": 25}}, ["query"])},
    {"name": "read_gmail", "description": "Read one Gmail message by id.", "inputSchema": schema_object({"message_id": {"type": "string"}}, ["message_id"])},
    {"name": "clean_gmail", "description": "Read and normalize one Gmail message into compact text and useful line candidates.", "inputSchema": schema_object({"message_id": {"type": "string"}}, ["message_id"])},
    {"name": "extract_receipt_items", "description": "Extract item candidates from receipt emails. Find the messages with search_gmail first and pass their ids.", "inputSchema": schema_object({"message_ids": {"type": "array", "items": {"type": "string"}}, "limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3}}, ["message_ids"])},
    {"name": "create_gmail_draft", "description": "Compose a Gmail draft. Drafts are never sent — you review and send it yourself in Gmail.", "inputSchema": schema_object({"to": {"type": "array", "items": {"type": "string"}}, "cc": {"type": "array", "items": {"type": "string"}}, "subject": {"type": "string"}, "body": {"type": "string"}, "thread_id": {"type": "string", "description": "Optional: reply within an existing thread."}}, ["subject"])},
    {"name": "list_gmail_labels", "description": "List Gmail labels.", "inputSchema": schema_object({"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100}})},
    {"name": "create_gmail_label", "description": "Create an agent-owned Gmail label.", "inputSchema": schema_object({"name": {"type": "string"}}, ["name"])},
    {"name": "mark_gmail_read", "description": "Mark a Gmail message as read.", "inputSchema": schema_object({"message_id": {"type": "string"}}, ["message_id"])},
    {"name": "archive_gmail", "description": "Archive a Gmail message.", "inputSchema": schema_object({"message_id": {"type": "string"}}, ["message_id"])},
    {"name": "add_gmail_labels", "description": "Add labels to a Gmail message.", "inputSchema": schema_object({"message_id": {"type": "string"}, "label_ids": {"type": "array", "items": {"type": "string"}}}, ["message_id", "label_ids"])},
    {"name": "list_calendars", "description": "List Google calendars.", "inputSchema": schema_object({"limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}})},
    {"name": "list_calendar_events", "description": "List calendar events in a time range.", "inputSchema": schema_object({"calendar_id": {"type": "string"}, "timeMin": {"type": "string"}, "timeMax": {"type": "string"}, "max_results": {"type": "integer", "minimum": 1, "maximum": 100}, "view": {"type": "string", "enum": ["full", "lean"], "default": "full", "description": "Use 'lean' for scheduling questions — returns only id, summary, start, end, location and status, about a quarter the size. Full events carry attendee lists, links and metadata that are rarely needed to answer what is on the calendar."}})},
    {"name": "calendar_freebusy", "description": "Check calendar free/busy.", "inputSchema": schema_object({"timeMin": {"type": "string"}, "timeMax": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}}, ["timeMin", "timeMax", "items"])},
    {"name": "create_agent_calendar_event", "description": "Create an event on the configured assistant-owned calendar only.", "inputSchema": schema_object({"event": {"type": "object"}, "calendar_id": {"type": "string"}}, ["event"])},
]


def tool_call(name, args):
    args = args or {}
    if name == "search_gmail":
        return bridge_post("/v1/gmail/search", args)
    if name == "read_gmail":
        return bridge_post("/v1/gmail/read", args)
    if name == "clean_gmail":
        return bridge_post("/v1/gmail/clean", args)
    if name == "extract_receipt_items":
        return bridge_post("/v1/gmail/extract_receipt_items", args)
    if name == "create_gmail_draft":
        return bridge_post("/v1/gmail/drafts/create", args)
    if name == "list_gmail_labels":
        return bridge_post("/v1/gmail/labels/list", {})
    if name == "create_gmail_label":
        return bridge_post("/v1/gmail/labels/create", args)
    if name == "mark_gmail_read":
        return bridge_post("/v1/gmail/modify", {"message_id": args["message_id"], "action": "mark_read"})
    if name == "archive_gmail":
        return bridge_post("/v1/gmail/modify", {"message_id": args["message_id"], "action": "archive"})
    if name == "add_gmail_labels":
        return bridge_post("/v1/gmail/modify", {"message_id": args["message_id"], "action": "add_labels", "label_ids": args["label_ids"]})
    if name == "list_calendars":
        return bridge_post("/v1/calendar/list", {})
    if name == "list_calendar_events":
        return bridge_post("/v1/calendar/events", args)
    if name == "calendar_freebusy":
        return bridge_post("/v1/calendar/freebusy", args)
    if name == "create_agent_calendar_event":
        return bridge_post("/v1/calendar/events/create", args)
    raise ToolError(f"unknown tool: {name}")


def tool_result(payload, is_error=False):
    text = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return {"content": [{"type": "text", "text": text}], "structuredContent": payload if isinstance(payload, (dict, list)) else {"message": text}, "isError": bool(is_error)}


def response(message_id, result=None, error=None):
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
        return response(message_id, {"protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "google-workspace-mcp-lite", "version": "0.1.0"}, "instructions": "Use for Gmail triage, Grocer meal planning, and calendar reads. Do not send or delete email."})
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return response(message_id, {})
    if method == "tools/list":
        return response(message_id, {"tools": TOOLS})
    if method == "tools/call":
        try:
            return response(message_id, tool_result(tool_call(params.get("name"), params.get("arguments") or {})))
        except Exception as exc:
            return response(message_id, tool_result(str(exc), True))
    if message_id is not None:
        return response(message_id, error={"code": -32601, "message": f"method not found: {method}"})
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "google-workspace-mcp-lite/0.1"

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
            result = handle_mcp(json.loads(self.rfile.read(length).decode("utf-8")))
            self.send_empty(202) if result is None else self.send_json(200, result)
        except McpError as exc:
            self.send_json(400, {"jsonrpc": "2.0", "error": {"code": exc.code, "message": exc.message}})
        except json.JSONDecodeError:
            self.send_json(400, {"jsonrpc": "2.0", "error": {"code": -32700, "message": "parse error"}})
        except Exception as exc:
            self.send_json(500, {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(exc)}})


if __name__ == "__main__":
    print(f"google workspace MCP lite listening on {HOST}:{PORT}; bridge={BRIDGE_URL}", file=sys.stderr, flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
