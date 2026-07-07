#!/usr/bin/env python3
import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


TOKEN = os.environ.get("MEMORY_BRIDGE_TOKEN", "")
HOST = os.environ.get("BRIDGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("BRIDGE_PORT", "8080"))
MEMORY_PATH = Path(os.environ.get("MEMORY_PATH", "/data/memory.json"))
MAX_BODY_BYTES = 128 * 1024


class BridgeError(Exception):
    def __init__(self, status, message):
        super().__init__(str(message))
        self.status = status
        self.message = message


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_store():
    if not MEMORY_PATH.exists():
        return {"memories": [], "proposals": []}
    return json.loads(MEMORY_PATH.read_text(encoding="utf-8"))


def save_store(store):
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MEMORY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MEMORY_PATH)


def parse_json_body(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    if length > MAX_BODY_BYTES:
        raise BridgeError(413, "request body too large")
    if length == 0:
        return {}
    try:
        body = json.loads(handler.rfile.read(length).decode("utf-8"))
    except json.JSONDecodeError:
        raise BridgeError(400, "invalid JSON body")
    if not isinstance(body, dict):
        raise BridgeError(400, "JSON body must be an object")
    return body


def require_auth(handler):
    if not TOKEN:
        raise BridgeError(503, "MEMORY_BRIDGE_TOKEN is not configured")
    if handler.headers.get("Authorization", "") != f"Bearer {TOKEN}":
        raise BridgeError(401, "invalid bridge token")


def clean_memory(body, status):
    statement = str(body.get("statement", "")).strip()
    if not statement:
        raise BridgeError(400, "statement is required")
    return {
        "id": body.get("id") or str(uuid.uuid4()),
        "type": body.get("type", "profile_preference"),
        "statement": statement,
        "source": body.get("source", ""),
        "confidence": body.get("confidence", "medium"),
        "sensitivity": body.get("sensitivity", "medium"),
        "status": status,
        "created_at": body.get("created_at") or now(),
        "updated_at": now(),
        "metadata": body.get("metadata", {}),
    }


def filter_items(items, query):
    typ = query.get("type")
    status = query.get("status")
    sensitivity = query.get("sensitivity")
    result = items
    if typ:
        result = [x for x in result if x.get("type") == typ]
    if status:
        result = [x for x in result if x.get("status") == status]
    if sensitivity:
        result = [x for x in result if x.get("sensitivity") == sensitivity]
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "memory-bridge/0.1"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {self.command} {self.path.split('?', 1)[0]} {fmt % args}", flush=True)

    def send_json(self, status, payload):
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def route(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if self.command == "GET" and path == "/health":
            return 200, {"ok": True}
        require_auth(self)
        store = load_store()
        if self.command == "GET" and path == "/schema":
            return 200, {"service": "memory-bridge", "tools": [
                "POST /v1/proposals",
                "GET /v1/proposals",
                "POST /v1/proposals/{id}/approve",
                "POST /v1/memories",
                "GET /v1/memories",
            ]}
        if self.command == "GET" and path == "/v1/proposals":
            return 200, {"proposals": filter_items(store["proposals"], {})}
        if self.command == "POST" and path == "/v1/proposals":
            item = clean_memory(parse_json_body(self), "proposed")
            store["proposals"].append(item)
            save_store(store)
            return 201, item
        if self.command == "POST" and path == "/v1/memories":
            item = clean_memory(parse_json_body(self), "approved")
            store["memories"].append(item)
            save_store(store)
            return 201, item
        if self.command == "GET" and path == "/v1/memories":
            return 200, {"memories": filter_items(store["memories"], {})}
        prefix = "/v1/proposals/"
        suffix = "/approve"
        if self.command == "POST" and path.startswith(prefix) and path.endswith(suffix):
            proposal_id = path[len(prefix):-len(suffix)]
            proposal = next((x for x in store["proposals"] if x.get("id") == proposal_id), None)
            if not proposal:
                raise BridgeError(404, "proposal not found")
            proposal["status"] = "approved"
            proposal["updated_at"] = now()
            store["memories"].append(proposal)
            save_store(store)
            return 200, proposal
        raise BridgeError(404, "not found")

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def handle_request(self):
        try:
            status, payload = self.route()
        except BridgeError as exc:
            status, payload = exc.status, {"error": exc.message}
        except Exception as exc:
            status, payload = 500, {"error": type(exc).__name__}
        self.send_json(status, payload)


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"memory-bridge listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
