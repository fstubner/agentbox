#!/usr/bin/env python3
"""Vikunja MCP: task management over the vikunja bridge.

Every write here is reversible and nothing deletes — marking a task for cleanup
adds a comment rather than removing anything.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from mcp_base import McpHandler, ToolError, schema_object, serve

BRIDGE_URL = os.environ.get("VIKUNJA_BRIDGE_URL", "http://vikunja-bridge:8080").rstrip("/")
BRIDGE_TOKEN = os.environ.get("VIKUNJA_BRIDGE_TOKEN", "")

TASK_FIELDS = {
    "project_id": {"type": "integer", "minimum": 1},
    "title": {"type": "string"},
    "description": {"type": "string"},
    "due_date": {"type": "string", "description": "Optional ISO-8601 date/time."},
    "priority": {"type": "integer", "minimum": 0, "maximum": 5},
}

TOOLS = [
    {"name": "list_projects", "title": "List task projects",
     "description": "List Vikunja projects visible to the assistant.",
     "inputSchema": schema_object({
         "search": {"type": "string"},
         "page": {"type": "integer", "minimum": 1, "default": 1},
         "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}})},
    {"name": "find_or_create_project", "title": "Find or create a project",
     "description": "Find a project by exact title, or create it if missing.",
     "inputSchema": schema_object({"title": {"type": "string"}, "description": {"type": "string"}}, ["title"])},
    {"name": "find_or_create_task", "title": "Find or create a task",
     "description": "Find an open task by exact title in a project, or create it. "
                    "Prefer this over create_task when a retry could duplicate work.",
     "inputSchema": schema_object(TASK_FIELDS, ["project_id", "title"])},
    {"name": "list_tasks", "title": "List tasks",
     "description": "List or search tasks. Use this before creating duplicates.",
     "inputSchema": schema_object({
         "search": {"type": "string"},
         "filter": {"type": "string", "description": "Optional Vikunja filter expression."},
         "page": {"type": "integer", "minimum": 1, "default": 1},
         "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
         "view": {"type": "string", "enum": ["full", "lean"], "default": "full",
                  "description": "Use 'lean' when picking, counting or ranking tasks — "
                                 "returns only id, title, done and priority, about a tenth "
                                 "the size. Use 'full' only when you need descriptions, "
                                 "dates or labels."}})},
    {"name": "create_task", "title": "Create a task", "description": "Create a task in a project.",
     "inputSchema": schema_object(TASK_FIELDS, ["project_id", "title"])},
    {"name": "update_task", "title": "Update a task",
     "description": "Update reversible task fields such as done, due date, description, priority, or percent done.",
     "inputSchema": schema_object({
         "task_id": {"type": "integer", "minimum": 1},
         "description": {"type": "string"},
         "done": {"type": "boolean"},
         "due_date": {"type": "string"},
         "priority": {"type": "integer", "minimum": 0, "maximum": 5},
         "percent_done": {"type": "number", "minimum": 0, "maximum": 1}}, ["task_id"])},
    {"name": "complete_task", "title": "Complete a task", "description": "Mark a task done.",
     "inputSchema": schema_object({"task_id": {"type": "integer", "minimum": 1},
                                   "comment": {"type": "string"}}, ["task_id"])},
    {"name": "add_task_comment", "title": "Add task comment",
     "description": "Add a note/comment to a task.",
     "inputSchema": schema_object({"task_id": {"type": "integer", "minimum": 1},
                                   "comment": {"type": "string"}}, ["task_id", "comment"])},
]


def bridge_request(method, path, payload=None, query=None):
    if not BRIDGE_TOKEN:
        raise ToolError("VIKUNJA_BRIDGE_TOKEN is not configured")
    url = f"{BRIDGE_URL}{path}"
    if query:
        clean = {k: v for k, v in query.items() if v not in (None, "")}
        if clean:
            url = f"{url}?{urllib.parse.urlencode(clean)}"
    data = None
    headers = {"Authorization": f"Bearer {BRIDGE_TOKEN}", "Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        raise ToolError(f"bridge returned HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:500]}")
    except urllib.error.URLError as exc:
        raise ToolError(f"bridge connection failed: {exc.reason}")


def task_payload(args):
    payload = {"title": str(args["title"]).strip(), "description": args.get("description", "")}
    for key in ("due_date", "priority"):
        if key in args:
            payload[key] = args[key]
    return payload


def dispatch(name, args):
    if name == "list_projects":
        return bridge_request("GET", "/v1/projects", query=args)

    if name == "find_or_create_project":
        title = str(args.get("title", "")).strip()
        if not title:
            raise ToolError("title is required")
        projects = bridge_request("GET", "/v1/projects", query={"search": title, "per_page": 100}) or []
        for project in projects:
            if str(project.get("title", "")).strip().casefold() == title.casefold():
                return {"project": project, "created": False}
        created = bridge_request("POST", "/v1/projects",
                                 payload={"title": title, "description": args.get("description", "")})
        return {"project": created, "created": True}

    if name == "find_or_create_task":
        title = str(args["title"]).strip()
        existing = bridge_request("GET", "/v1/tasks", query={
            "search": title, "per_page": 100, "view": "full"}) or []
        for task in existing:
            if str(task.get("title", "")).strip() == title and not task.get("done"):
                return task
        return bridge_request("POST", f"/v1/projects/{int(args['project_id'])}/tasks",
                              payload=task_payload(args))

    if name == "list_tasks":
        return bridge_request("GET", "/v1/tasks", query={
            "search": args.get("search", ""), "filter": args.get("filter", ""),
            "page": args.get("page", 1), "per_page": args.get("per_page", 50),
            "view": args.get("view", "")})

    if name == "create_task":
        return bridge_request("POST", f"/v1/projects/{int(args['project_id'])}/tasks",
                              payload=task_payload(args))

    if name == "update_task":
        payload = {k: v for k, v in args.items() if k != "task_id"}
        return bridge_request("PATCH", f"/v1/tasks/{int(args['task_id'])}", payload=payload)

    if name == "complete_task":
        task_id = int(args["task_id"])
        result = bridge_request("PATCH", f"/v1/tasks/{task_id}",
                                payload={"done": True, "percent_done": 1})
        comment = args.get("comment", "").strip()
        if comment:
            bridge_request("POST", f"/v1/tasks/{task_id}/comments", payload={"comment": comment})
        return result

    if name == "add_task_comment":
        return bridge_request("POST", f"/v1/tasks/{int(args['task_id'])}/comments",
                              payload={"comment": args["comment"]})

    raise ToolError(f"unknown tool: {name}")


class VikunjaMcp(McpHandler):
    service_name = "vikunja-mcp"
    instructions = "Use for the operator's task backend. List before creating to avoid duplicates."
    tools = TOOLS
    dispatch = staticmethod(dispatch)
    bridge_url = BRIDGE_URL
    shared_token = os.environ.get("VIKUNJA_MCP_SHARED_TOKEN", "")


if __name__ == "__main__":
    serve(VikunjaMcp)
