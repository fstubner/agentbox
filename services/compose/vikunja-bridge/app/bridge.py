#!/usr/bin/env python3
"""Vikunja bridge on the shared bridge_base. Holds the Vikunja API token and
exposes a narrow task/project API to the assistant."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from bridge_base import BridgeError, BridgeHandler, serve

VIKUNJA_URL = os.environ.get("VIKUNJA_URL", "http://vikunja:3456").rstrip("/")
VIKUNJA_API_TOKEN = os.environ.get("VIKUNJA_API_TOKEN", "")


def vikunja_request(method, path, payload=None, query=None):
    url = f"{VIKUNJA_URL}/api/v1{path}"
    if query:
        clean_query = {k: v for k, v in query.items() if v not in (None, "")}
        if clean_query:
            url = f"{url}?{urllib.parse.urlencode(clean_query, doseq=True)}"
    data = None
    headers = {"Authorization": f"Bearer {VIKUNJA_API_TOKEN}", "Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
            return json.loads(body.decode("utf-8")) if body else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw[:500]
        raise BridgeError(exc.code, {"vikunja_error": detail})
    except urllib.error.URLError as exc:
        raise BridgeError(502, {"vikunja_error": str(exc.reason)})


def clean_task_payload(body, allow_title=True):
    allowed = {"description", "done", "due_date", "end_date", "hex_color",
               "percent_done", "priority", "start_date", "title"}
    if not allow_title:
        allowed.discard("title")
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


def query_of(handler) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)


def first(query, key, default):
    value = query.get(key, [default])
    return value[0] if value else default


# --- projection pushdown (docs/context-economy.md §1) ------------------------
#
# A lean view emits only the fields a task-picking agent acts on, so the excess
# is never generated rather than compressed after the fact. `full` stays the
# default until the A/B eval shows lean costs no task accuracy.

LEAN_TASK_FIELDS = ("id", "title", "done", "priority")
TASK_VIEWS = {"full", "lean"}


def require_view(query, allowed=TASK_VIEWS, default="full"):
    view = first(query, "view", default)
    if view not in allowed:
        raise BridgeError(400, f"view must be one of: {', '.join(sorted(allowed))}")
    return view


def project_tasks(payload, fields=LEAN_TASK_FIELDS):
    """Narrow a task list to `fields`. Absent keys are omitted, not nulled, so
    the projection never invents data. Non-list payloads pass through."""
    if not isinstance(payload, list):
        return payload
    return [{k: item[k] for k in fields if k in item}
            for item in payload if isinstance(item, dict)]


# --- static routes ----------------------------------------------------------


def get_schema(handler, body):
    return 200, {"service": "vikunja-bridge", "tools": [
        "GET /v1/projects", "POST /v1/projects", "GET /v1/tasks", "GET /v1/tasks/{id}",
        "POST /v1/projects/{project_id}/tasks", "PATCH /v1/tasks/{id}",
        "POST /v1/tasks/{id}/comments",
    ], "views": {
        "GET /v1/tasks": {
            "param": "view", "default": "full", "values": sorted(TASK_VIEWS),
            "lean_fields": list(LEAN_TASK_FIELDS),
            "hint": "use view=lean when picking or ranking tasks; "
                    "re-read a single task with GET /v1/tasks/{id} for full detail",
        },
    }}


def list_projects(handler, body):
    q = query_of(handler)
    return 200, vikunja_request("GET", "/projects", query={
        "page": first(q, "page", "1"), "per_page": first(q, "per_page", "50"),
        "s": first(q, "search", ""),
    })


def create_project(handler, body):
    title = str((body or {}).get("title", "")).strip()
    if not title:
        raise BridgeError(400, "title is required")
    return 201, vikunja_request("PUT", "/projects",
                                {"title": title, "description": (body or {}).get("description", "")})


def list_tasks(handler, body):
    q = query_of(handler)
    view = require_view(q)
    tasks = vikunja_request("GET", "/tasks", query={
        "page": first(q, "page", "1"), "per_page": first(q, "per_page", "50"),
        "s": first(q, "search", ""), "sort_by": first(q, "sort_by", ""),
        "order_by": first(q, "order_by", ""), "filter": first(q, "filter", ""),
        "expand": first(q, "expand", ""),
    })
    return 200, (project_tasks(tasks) if view == "lean" else tasks)


# --- dynamic routes (via route_fallback) ------------------------------------


def get_task(handler, task_id):
    return 200, vikunja_request("GET", f"/tasks/{task_id}",
                                query={"expand": first(query_of(handler), "expand", "")})


def create_task(handler, project_id, body):
    return 201, vikunja_request("PUT", f"/projects/{project_id}/tasks", clean_task_payload(body or {}))


def patch_task(handler, task_id, body):
    payload = clean_task_payload(body or {}, allow_title=False)
    if not payload:
        raise BridgeError(400, "no supported fields provided")
    current = vikunja_request("GET", f"/tasks/{task_id}")
    current.update(payload)
    return 200, vikunja_request("POST", f"/tasks/{task_id}", current)


def add_comment(handler, task_id, body):
    comment = str((body or {}).get("comment", "")).strip()
    if not comment:
        raise BridgeError(400, "comment is required")
    return 201, vikunja_request("PUT", f"/tasks/{task_id}/comments", {"comment": comment})


class VikunjaBridge(BridgeHandler):
    server_version = "vikunja-bridge/1.0"
    bridge_token = os.environ.get("VIKUNJA_BRIDGE_TOKEN", "")
    routes = {
        ("GET", "/schema"): get_schema,
        ("GET", "/v1/projects"): list_projects,
        ("POST", "/v1/projects"): create_project,
        ("GET", "/v1/tasks"): list_tasks,
    }

    def route_fallback(self, method: str, path: str, body: Any):
        m = re.fullmatch(r"/v1/tasks/(\d+)", path)
        if m and method == "GET":
            return get_task(self, require_int(m.group(1), "task_id"))
        if m and method == "PATCH":
            return patch_task(self, require_int(m.group(1), "task_id"), body)
        m = re.fullmatch(r"/v1/projects/(\d+)/tasks", path)
        if m and method == "POST":
            return create_task(self, require_int(m.group(1), "project_id"), body)
        m = re.fullmatch(r"/v1/tasks/(\d+)/comments", path)
        if m and method == "POST":
            return add_comment(self, require_int(m.group(1), "task_id"), body)
        raise BridgeError(404, "not found")


def require_runtime_config():
    missing = [n for n, v in (("VIKUNJA_API_TOKEN", VIKUNJA_API_TOKEN),
                              ("VIKUNJA_BRIDGE_TOKEN", VikunjaBridge.bridge_token)) if not v]
    if missing:
        raise RuntimeError(f"missing required env: {', '.join(missing)}")


if __name__ == "__main__":
    require_runtime_config()
    serve(VikunjaBridge)
