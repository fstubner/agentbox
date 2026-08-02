#!/usr/bin/env python3
"""Google Workspace MCP: Gmail and Calendar over the google-workspace bridge.

Sending is not exposed. Composing stops at a draft, which leaves the
irreversible step with a human who reads it in Gmail first.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from mcp_base import McpHandler, ToolError, schema_object, serve

BRIDGE_URL = os.environ.get("GOOGLE_BRIDGE_URL", "http://google-workspace-bridge:8080").rstrip("/")
BRIDGE_TOKEN = os.environ.get("GOOGLE_BRIDGE_TOKEN", "")

VIEW = {"type": "string", "enum": ["full", "lean"], "default": "full",
        "description": "Use 'lean' for scheduling questions — returns only id, summary, "
                       "start, end, location and status, about a quarter the size. Full "
                       "events carry attendee lists, links and metadata rarely needed to "
                       "answer what is on the calendar."}

TOOLS = [
    {"name": "search_gmail", "description": "Search Gmail messages.", "inputSchema": schema_object({"query": {"type": "string"}, "max_results": {"type": "integer", "minimum": 1, "maximum": 25}}, ["query"])},
    {"name": "read_gmail", "description": "Read one Gmail message by id.", "inputSchema": schema_object({"message_id": {"type": "string"}}, ["message_id"])},
    {"name": "clean_gmail", "description": "Read and normalise one Gmail message into compact text and useful line candidates.", "inputSchema": schema_object({"message_id": {"type": "string"}}, ["message_id"])},
    {"name": "create_gmail_draft", "description": "Compose a Gmail draft. Drafts are never sent — you review and send it yourself in Gmail.", "inputSchema": schema_object({"to": {"type": "array", "items": {"type": "string"}}, "cc": {"type": "array", "items": {"type": "string"}}, "subject": {"type": "string"}, "body": {"type": "string"}, "thread_id": {"type": "string"}}, ["subject"])},
    {"name": "list_gmail_labels", "description": "List Gmail labels.", "inputSchema": schema_object({"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100}})},
    {"name": "create_gmail_label", "description": "Create an agent-owned Gmail label.", "inputSchema": schema_object({"name": {"type": "string"}}, ["name"])},
    {"name": "mark_gmail_read", "description": "Mark a Gmail message as read.", "inputSchema": schema_object({"message_id": {"type": "string"}}, ["message_id"])},
    {"name": "archive_gmail", "description": "Archive a Gmail message.", "inputSchema": schema_object({"message_id": {"type": "string"}}, ["message_id"])},
    {"name": "add_gmail_labels", "description": "Add agent-owned labels to a Gmail message.", "inputSchema": schema_object({"message_id": {"type": "string"}, "label_ids": {"type": "array", "items": {"type": "string"}}}, ["message_id", "label_ids"])},
    {"name": "list_calendars", "description": "List Google calendars.", "inputSchema": schema_object({"limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}})},
    {"name": "list_calendar_events", "description": "List calendar events in a time range.", "inputSchema": schema_object({"calendar_id": {"type": "string"}, "timeMin": {"type": "string"}, "timeMax": {"type": "string"}, "max_results": {"type": "integer", "minimum": 1, "maximum": 100}, "view": VIEW})},
    {"name": "calendar_freebusy", "description": "Check calendar free/busy.", "inputSchema": schema_object({"timeMin": {"type": "string"}, "timeMax": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}}, ["timeMin", "timeMax", "items"])},
    {"name": "create_agent_calendar_event", "description": "Create an event on the assistant-owned calendar only. Attendees are refused; it cannot notify anyone.", "inputSchema": schema_object({"event": {"type": "object"}, "calendar_id": {"type": "string"}}, ["event"])},
]

ROUTES = {
    "search_gmail": "/v1/gmail/search",
    "read_gmail": "/v1/gmail/read",
    "clean_gmail": "/v1/gmail/clean",
    "create_gmail_draft": "/v1/gmail/drafts/create",
    "create_gmail_label": "/v1/gmail/labels/create",
    "list_calendar_events": "/v1/calendar/events",
    "calendar_freebusy": "/v1/calendar/freebusy",
    "create_agent_calendar_event": "/v1/calendar/events/create",
}

MODIFY = {"mark_gmail_read": "mark_read", "archive_gmail": "archive", "add_gmail_labels": "add_labels"}


def bridge_post(path, payload=None):
    if not BRIDGE_TOKEN:
        raise ToolError("GOOGLE_BRIDGE_TOKEN is not configured")
    data = json.dumps(payload or {}).encode("utf-8")
    headers = {"Authorization": f"Bearer {BRIDGE_TOKEN}", "Accept": "application/json",
               "Content-Type": "application/json"}
    req = urllib.request.Request(f"{BRIDGE_URL}{path}", data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        raise ToolError(f"bridge HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:500]}")
    except urllib.error.URLError as exc:
        raise ToolError(f"bridge connection failed: {exc.reason}")


def dispatch(name, args):
    if name in ("list_gmail_labels", "list_calendars"):
        return bridge_post("/v1/gmail/labels/list" if name == "list_gmail_labels" else "/v1/calendar/list", {})
    if name in MODIFY:
        payload = {"message_id": args["message_id"], "action": MODIFY[name]}
        if name == "add_gmail_labels":
            payload["label_ids"] = args["label_ids"]
        return bridge_post("/v1/gmail/modify", payload)
    if name in ROUTES:
        return bridge_post(ROUTES[name], args)
    raise ToolError(f"unknown tool: {name}")


class GoogleWorkspaceMcp(McpHandler):
    service_name = "google-workspace-mcp-lite"
    instructions = ("Use for Gmail triage and calendar reads. You cannot send mail or "
                    "delete anything; compose drafts instead.")
    tools = TOOLS
    dispatch = staticmethod(dispatch)
    bridge_url = BRIDGE_URL
    shared_token = os.environ.get("GOOGLE_MCP_SHARED_TOKEN", "")


if __name__ == "__main__":
    serve(GoogleWorkspaceMcp)
