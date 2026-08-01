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
BRIDGE_URL = os.environ.get("VIKUNJA_BRIDGE_URL", "http://vikunja-bridge:8080").rstrip("/")
BRIDGE_TOKEN = os.environ.get("VIKUNJA_BRIDGE_TOKEN", "")
MCP_SHARED_TOKEN = os.environ.get("VIKUNJA_MCP_SHARED_TOKEN", "")
PROTOCOL_VERSION = "2025-06-18"
MAX_BODY_BYTES = 128 * 1024


class McpError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class ToolError(Exception):
    pass


def bridge_request(method, path, payload=None, query=None):
    if not BRIDGE_TOKEN:
        raise ToolError("VIKUNJA_BRIDGE_TOKEN is not configured")

    url = f"{BRIDGE_URL}{path}"
    if query:
        clean_query = {k: v for k, v in query.items() if v not in (None, "")}
        if clean_query:
            url = f"{url}?{urllib.parse.urlencode(clean_query)}"

    data = None
    headers = {
        "Authorization": f"Bearer {BRIDGE_TOKEN}",
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise ToolError(f"bridge returned HTTP {exc.code}: {raw[:500]}")
    except urllib.error.URLError as exc:
        raise ToolError(f"bridge connection failed: {exc.reason}")


def schema_object(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TOOLS = [
    {
        "name": "list_projects",
        "title": "List task projects",
        "description": "List Vikunja projects visible to the assistant.",
        "inputSchema": schema_object({
            "search": {"type": "string", "description": "Optional project title search."},
            "page": {"type": "integer", "minimum": 1, "default": 1},
            "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
        }),
    },
    {
        "name": "find_or_create_project",
        "title": "Find or create a project",
        "description": "Find a project by exact title, or create it if missing.",
        "inputSchema": schema_object({
            "title": {"type": "string", "description": "Project title."},
            "description": {"type": "string", "description": "Description to use if creating the project."},
        }, ["title"]),
    },
    {
        "name": "list_tasks",
        "title": "List tasks",
        "description": "List or search tasks. Use this before creating duplicates.",
        "inputSchema": schema_object({
            "search": {"type": "string", "description": "Optional task search text."},
            "filter": {"type": "string", "description": "Optional Vikunja filter expression."},
            "page": {"type": "integer", "minimum": 1, "default": 1},
            "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
            "view": {
                "type": "string", "enum": ["full", "lean"], "default": "full",
                "description": "Use 'lean' when picking, counting or ranking tasks — "
                               "returns only id, title, done and priority, about a "
                               "tenth the size. Use 'full' only when you need "
                               "descriptions, dates or labels.",
            },
        }),
    },
    {
        "name": "create_task",
        "title": "Create a task",
        "description": "Create a task in a project.",
        "inputSchema": schema_object({
            "project_id": {"type": "integer", "minimum": 1},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "due_date": {"type": "string", "description": "Optional ISO-8601 date/time."},
            "priority": {"type": "integer", "minimum": 0, "maximum": 5},
        }, ["project_id", "title"]),
    },
    {
        "name": "update_task",
        "title": "Update a task",
        "description": "Update reversible task fields such as done, due date, description, priority, or percent done.",
        "inputSchema": schema_object({
            "task_id": {"type": "integer", "minimum": 1},
            "description": {"type": "string"},
            "done": {"type": "boolean"},
            "due_date": {"type": "string", "description": "Optional ISO-8601 date/time."},
            "priority": {"type": "integer", "minimum": 0, "maximum": 5},
            "percent_done": {"type": "number", "minimum": 0, "maximum": 1},
        }, ["task_id"]),
    },
    {
        "name": "complete_task",
        "title": "Complete a task",
        "description": "Mark a task done.",
        "inputSchema": schema_object({
            "task_id": {"type": "integer", "minimum": 1},
            "comment": {"type": "string", "description": "Optional completion note."},
        }, ["task_id"]),
    },
    {
        "name": "add_task_comment",
        "title": "Add task comment",
        "description": "Add a note/comment to a task.",
        "inputSchema": schema_object({
            "task_id": {"type": "integer", "minimum": 1},
            "comment": {"type": "string"},
        }, ["task_id", "comment"]),
    },
    {
        "name": "mark_cleanup_candidate",
        "title": "Mark task as cleanup candidate",
        "description": "Add a cleanup-review comment to a task instead of deleting it.",
        "inputSchema": schema_object({
            "task_id": {"type": "integer", "minimum": 1},
            "reason": {"type": "string"},
        }, ["task_id", "reason"]),
    },
    {
        "name": "cleanup_report",
        "title": "Task cleanup report",
        "description": "List tasks matching cleanup-related search text for user review.",
        "inputSchema": schema_object({
            "search": {"type": "string", "default": "cleanup candidate"},
            "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
        }),
    },
]


def tool_call(name, args):
    args = args or {}

    if name == "list_projects":
        return bridge_request("GET", "/v1/projects", query=args)

    if name == "find_or_create_project":
        title = args.get("title", "").strip()
        if not title:
            raise ToolError("title is required")
        projects = bridge_request("GET", "/v1/projects", query={"search": title, "per_page": 100}) or []
        for project in projects:
            if str(project.get("title", "")).strip().casefold() == title.casefold():
                return {"project": project, "created": False}
        created = bridge_request("POST", "/v1/projects", payload={
            "title": title,
            "description": args.get("description", ""),
        })
        return {"project": created, "created": True}

    if name == "list_tasks":
        return bridge_request("GET", "/v1/tasks", query={
            "search": args.get("search", ""),
            "filter": args.get("filter", ""),
            "page": args.get("page", 1),
            "per_page": args.get("per_page", 50),
            "view": args.get("view", ""),
        })

    if name == "create_task":
        project_id = int(args["project_id"])
        payload = {"title": args["title"], "description": args.get("description", "")}
        for key in ("due_date", "priority"):
            if key in args:
                payload[key] = args[key]
        return bridge_request("POST", f"/v1/projects/{project_id}/tasks", payload=payload)

    if name == "update_task":
        task_id = int(args["task_id"])
        payload = {k: v for k, v in args.items() if k != "task_id"}
        if not payload:
            raise ToolError("at least one update field is required")
        return bridge_request("PATCH", f"/v1/tasks/{task_id}", payload=payload)

    if name == "complete_task":
        task_id = int(args["task_id"])
        result = bridge_request("PATCH", f"/v1/tasks/{task_id}", payload={"done": True, "percent_done": 1})
        comment = args.get("comment", "").strip()
        if comment:
            bridge_request("POST", f"/v1/tasks/{task_id}/comments", payload={"comment": comment})
        return result

    if name == "add_task_comment":
        task_id = int(args["task_id"])
        return bridge_request("POST", f"/v1/tasks/{task_id}/comments", payload={"comment": args["comment"]})

    if name == "mark_cleanup_candidate":
        task_id = int(args["task_id"])
        reason = args.get("reason", "").strip()
        return bridge_request("POST", f"/v1/tasks/{task_id}/comments", payload={
            "comment": f"cleanup candidate: {reason}",
        })

    if name == "cleanup_report":
        search = args.get("search", "cleanup candidate")
        return bridge_request("GET", "/v1/tasks", query={"search": search, "per_page": args.get("per_page", 50)})

    raise ToolError(f"unknown tool: {name}")


def tool_result(payload, is_error=False):
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, sort_keys=True)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload if isinstance(payload, (dict, list)) else {"message": text},
        "isError": bool(is_error),
    }


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
        requested = params.get("protocolVersion") or PROTOCOL_VERSION
        return mcp_response(message_id, {
            "protocolVersion": requested,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": "vikunja-tasks",
                "title": "Vikunja Task Tools",
                "version": "0.1.0",
            },
            "instructions": "Use these tools for personal task and lightweight project management. Do not delete tasks; mark cleanup candidates for review.",
        })

    if method == "notifications/initialized":
        return None

    if method == "ping":
        return mcp_response(message_id, {})

    if method == "tools/list":
        return mcp_response(message_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            return mcp_response(message_id, tool_result(tool_call(name, arguments)))
        except Exception as exc:
            return mcp_response(message_id, tool_result(str(exc), is_error=True))

    if message_id is not None:
        return mcp_response(message_id, error={"code": -32601, "message": f"method not found: {method}"})
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "vikunja-mcp/0.1"

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

    def require_origin_ok(self):
        origin = self.headers.get("Origin")
        if not origin:
            return
        parsed = urllib.parse.urlparse(origin)
        if parsed.hostname not in ("127.0.0.1", "localhost", *(os.environ.get("VIKUNJA_MCP_ALLOWED_ORIGINS", "agentbox.local").split(","))):
            raise McpError(-32000, "origin not allowed")

    def require_auth_ok(self):
        if not MCP_SHARED_TOKEN:
            return
        header = self.headers.get("Authorization", "")
        if header != f"Bearer {MCP_SHARED_TOKEN}":
            raise McpError(-32001, "invalid MCP bearer token")

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
            self.require_origin_ok()
            self.require_auth_ok()
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY_BYTES:
                self.send_json(413, {"error": "request too large"})
                return
            raw = self.rfile.read(length)
            message = json.loads(raw.decode("utf-8"))
            response = handle_mcp(message)
            if response is None:
                self.send_empty(202)
            else:
                self.send_json(200, response)
        except McpError as exc:
            self.send_json(400, {"jsonrpc": "2.0", "error": {"code": exc.code, "message": exc.message}})
        except json.JSONDecodeError:
            self.send_json(400, {"jsonrpc": "2.0", "error": {"code": -32700, "message": "parse error"}})
        except Exception as exc:
            self.send_json(500, {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(exc)}})


if __name__ == "__main__":
    print(f"vikunja MCP listening on {HOST}:{PORT}; bridge={BRIDGE_URL}", file=sys.stderr, flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
