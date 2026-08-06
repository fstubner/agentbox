"""Google Workspace MCP: Gmail and Calendar over the google-workspace bridge.

Sending is not exposed. Composing stops at a draft, which leaves the
irreversible step with a human who reads it in Gmail first.
"""
from __future__ import annotations

from mcp_base import ToolError, schema_object

from integrations._client import bridge_client

_post_client = bridge_client("GOOGLE", "google-workspace-bridge", timeout=30)

def bridge_post(path, payload=None):
    return _post_client("POST", path, payload=payload or {})

VIEW = {"type": "string", "enum": ["full", "lean"], "default": "full",
        "description": "Use 'lean' for scheduling questions — returns only id, summary, "
                       "start, end, location and status, about a quarter the size. Full "
                       "events carry attendee lists, links and metadata rarely needed to "
                       "answer what is on the calendar."}

TOOLS = [
    {"name": "search_gmail",
     "description": "Search Gmail messages.",
     "inputSchema": schema_object({
         "query": {"type": "string"},
         "max_results": {"type": "integer", "minimum": 1, "maximum": 25}},
         ["query"])},

    {"name": "read_gmail",
     "description": "Read one Gmail message by id.",
     "inputSchema": schema_object({"message_id": {"type": "string"}},
                                  ["message_id"])},

    {"name": "clean_gmail",
     "description": "Read and normalise one Gmail message into compact text "
                    "and useful line candidates.",
     "inputSchema": schema_object({"message_id": {"type": "string"}},
                                  ["message_id"])},

    {"name": "create_gmail_draft",
     "description": "Compose a Gmail draft. Drafts are never sent — you review "
                    "and send it yourself in Gmail.",
     "inputSchema": schema_object({
         "to": {"type": "array", "items": {"type": "string"}},
         "cc": {"type": "array", "items": {"type": "string"}},
         "subject": {"type": "string"},
         "body": {"type": "string"},
         "thread_id": {"type": "string"}},
         ["subject"])},

    {"name": "list_gmail_labels",
     "description": "List Gmail labels.",
     "inputSchema": schema_object({
         "limit": {"type": "integer", "minimum": 1, "maximum": 200,
                   "default": 100}})},

    {"name": "create_gmail_label",
     "description": "Create an agent-owned Gmail label.",
     "inputSchema": schema_object({"name": {"type": "string"}}, ["name"])},

    {"name": "mark_gmail_read",
     "description": "Mark a Gmail message as read.",
     "inputSchema": schema_object({"message_id": {"type": "string"}},
                                  ["message_id"])},

    {"name": "archive_gmail",
     "description": "Archive a Gmail message.",
     "inputSchema": schema_object({"message_id": {"type": "string"}},
                                  ["message_id"])},

    {"name": "add_gmail_labels",
     "description": "Add agent-owned labels to a Gmail message.",
     "inputSchema": schema_object({
         "message_id": {"type": "string"},
         "label_ids": {"type": "array", "items": {"type": "string"}}},
         ["message_id", "label_ids"])},

    {"name": "list_calendars",
     "description": "List Google calendars.",
     "inputSchema": schema_object({
         "limit": {"type": "integer", "minimum": 1, "maximum": 100,
                   "default": 50}})},

    {"name": "list_calendar_events",
     "description": "List calendar events in a time range.",
     "inputSchema": schema_object({
         "calendar_id": {"type": "string"},
         "timeMin": {"type": "string"},
         "timeMax": {"type": "string"},
         "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
         "view": VIEW})},

    {"name": "calendar_freebusy",
     "description": "Check calendar free/busy.",
     "inputSchema": schema_object({
         "timeMin": {"type": "string"},
         "timeMax": {"type": "string"},
         "items": {"type": "array", "items": {"type": "string"}}},
         ["timeMin", "timeMax", "items"])},

    {"name": "create_agent_calendar_event",
     "description": "Create an event on the assistant-owned calendar only. "
                    "Attendees are refused; it cannot notify anyone.",
     "inputSchema": schema_object({
         "event": {"type": "object"},
         "calendar_id": {"type": "string"}},
         ["event"])},
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
