#!/usr/bin/env python3
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


VIKUNJA_URL = os.environ.get("VIKUNJA_URL", "http://vikunja:3456").rstrip("/")
VIKUNJA_API_TOKEN = os.environ.get("VIKUNJA_API_TOKEN", "")
BRIDGE_TOKEN = os.environ.get("VIKUNJA_BRIDGE_TOKEN", "")
HOST = os.environ.get("BRIDGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("BRIDGE_PORT", "8080"))
MAX_BODY_BYTES = 64 * 1024


class BridgeError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def require_runtime_config():
    missing = []
    if not VIKUNJA_API_TOKEN:
        missing.append("VIKUNJA_API_TOKEN")
    if not BRIDGE_TOKEN:
        missing.append("VIKUNJA_BRIDGE_TOKEN")
    if missing:
        raise RuntimeError(f"missing required env: {', '.join(missing)}")


def vikunja_request(method, path, payload=None, query=None):
    url = f"{VIKUNJA_URL}/api/v1{path}"
    if query:
        clean_query = {k: v for k, v in query.items() if v not in (None, "")}
        if clean_query:
            url = f"{url}?{urllib.parse.urlencode(clean_query, doseq=True)}"

    data = None
    headers = {
        "Authorization": f"Bearer {VIKUNJA_API_TOKEN}",
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, method=method, headers=headers)

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
            if not body:
                return None
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw[:500]
        raise BridgeError(exc.code, {"vikunja_error": detail})
    except urllib.error.URLError as exc:
        raise BridgeError(502, {"vikunja_error": str(exc.reason)})


def parse_json_body(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    if length > MAX_BODY_BYTES:
        raise BridgeError(413, "request body too large")
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        body = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        raise BridgeError(400, "invalid JSON body")
    if not isinstance(body, dict):
        raise BridgeError(400, "JSON body must be an object")
    return body


def clean_task_payload(body, allow_title=True):
    allowed = {
        "description",
        "done",
        "due_date",
        "end_date",
        "hex_color",
        "percent_done",
        "priority",
        "start_date",
        "title",
    }
    if not allow_title:
        allowed.remove("title")
    payload = {k: v for k, v in body.items() if k in allowed}
    if allow_title and not str(payload.get("title", "")).strip():
        raise BridgeError(400, "title is required")
    return payload


def require_int(value, name):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise BridgeError(400, f"{name} must be an integer")
    if parsed <= 0:
        raise BridgeError(400, f"{name} must be positive")
    return parsed


def first(query, key, default):
    value = query.get(key, [default])
    return value[0] if value else default


class Handler(BaseHTTPRequestHandler):
    server_version = "vikunja-bridge/0.1"

    def log_message(self, fmt, *args):
        safe_path = self.path.split("?", 1)[0]
        print(f"{self.address_string()} {self.command} {safe_path} {fmt % args}", flush=True)

    def send_json(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def require_auth(self):
        header = self.headers.get("Authorization", "")
        expected = f"Bearer {BRIDGE_TOKEN}"
        if header != expected:
            raise BridgeError(401, "invalid bridge token")

    def route(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)

        if self.command == "GET" and path == "/health":
            return 200, {"ok": True}

        self.require_auth()

        if self.command == "GET" and path == "/schema":
            return 200, {
                "service": "vikunja-bridge",
                "tools": [
                    "GET /v1/projects",
                    "POST /v1/projects",
                    "GET /v1/tasks",
                    "GET /v1/tasks/{id}",
                    "POST /v1/projects/{project_id}/tasks",
                    "PATCH /v1/tasks/{id}",
                    "POST /v1/tasks/{id}/comments",
                ],
            }

        if self.command == "GET" and path == "/v1/projects":
            return 200, vikunja_request("GET", "/projects", query={
                "page": first(query, "page", "1"),
                "per_page": first(query, "per_page", "50"),
                "s": first(query, "search", ""),
            })

        if self.command == "POST" and path == "/v1/projects":
            body = parse_json_body(self)
            title = str(body.get("title", "")).strip()
            if not title:
                raise BridgeError(400, "title is required")
            payload = {"title": title, "description": body.get("description", "")}
            return 201, vikunja_request("PUT", "/projects", payload)

        if self.command == "GET" and path == "/v1/tasks":
            return 200, vikunja_request("GET", "/tasks", query={
                "page": first(query, "page", "1"),
                "per_page": first(query, "per_page", "50"),
                "s": first(query, "search", ""),
                "sort_by": first(query, "sort_by", ""),
                "order_by": first(query, "order_by", ""),
                "filter": first(query, "filter", ""),
                "expand": first(query, "expand", ""),
            })

        match = re.fullmatch(r"/v1/tasks/(\d+)", path)
        if self.command == "GET" and match:
            task_id = require_int(match.group(1), "task_id")
            return 200, vikunja_request("GET", f"/tasks/{task_id}", query={
                "expand": first(query, "expand", ""),
            })

        match = re.fullmatch(r"/v1/projects/(\d+)/tasks", path)
        if self.command == "POST" and match:
            project_id = require_int(match.group(1), "project_id")
            body = parse_json_body(self)
            return 201, vikunja_request(
                "PUT",
                f"/projects/{project_id}/tasks",
                clean_task_payload(body),
            )

        match = re.fullmatch(r"/v1/tasks/(\d+)", path)
        if self.command == "PATCH" and match:
            task_id = require_int(match.group(1), "task_id")
            body = parse_json_body(self)
            payload = clean_task_payload(body, allow_title=False)
            if not payload:
                raise BridgeError(400, "no supported fields provided")
            current = vikunja_request("GET", f"/tasks/{task_id}")
            current.update(payload)
            return 200, vikunja_request("POST", f"/tasks/{task_id}", current)

        match = re.fullmatch(r"/v1/tasks/(\d+)/comments", path)
        if self.command == "POST" and match:
            task_id = require_int(match.group(1), "task_id")
            body = parse_json_body(self)
            comment = str(body.get("comment", "")).strip()
            if not comment:
                raise BridgeError(400, "comment is required")
            return 201, vikunja_request("PUT", f"/tasks/{task_id}/comments", {"comment": comment})

        raise BridgeError(404, "not found")

    def handle_request(self):
        try:
            status, payload = self.route()
            self.send_json(status, payload)
        except BridgeError as exc:
            self.send_json(exc.status, {"error": exc.message})

    do_GET = handle_request
    do_POST = handle_request
    do_PATCH = handle_request


if __name__ == "__main__":
    require_runtime_config()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"vikunja bridge listening on {HOST}:{PORT}; upstream={VIKUNJA_URL}", flush=True)
    server.serve_forever()
