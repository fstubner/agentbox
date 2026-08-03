#!/usr/bin/env python3
"""Memory MCP: the assistant proposes and reads; it cannot approve or store.

Approval and direct writes are operator actions behind a review token the
assistant never holds — see memory-bridge. Exposing them here once meant the
review gate did not exist, guarded only by a sentence in a tool description.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from mcp_base import McpHandler, ToolError, schema_object, serve

BRIDGE_URL = os.environ.get("MEMORY_BRIDGE_URL", "http://memory-bridge:8080").rstrip("/")
BRIDGE_TOKEN = os.environ.get("MEMORY_BRIDGE_TOKEN", "")

MEMORY_FIELDS = {
    "type": {"type": "string", "description": "health_profile, food_preference, project_context, workflow_rule, household_preference, etc."},
    "statement": {"type": "string"},
    "source": {"type": "string"},
    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    "sensitivity": {"type": "string", "enum": ["low", "medium", "high"]},
    "metadata": {"type": "object"},
}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}

TOOLS = [
    {"name": "propose_memory", "description": "Create a memory proposal for user review.", "inputSchema": schema_object(MEMORY_FIELDS, ["statement"])},
    {"name": "list_memory_proposals", "description": "List pending memory proposals.", "inputSchema": schema_object({"limit": LIMIT})},
    {"name": "search_memories", "description": "List stored memories for context retrieval.", "inputSchema": schema_object({"limit": LIMIT})},
    {"name": "review_own_activity",
     "title": "Review your own recent activity",
     "description":
         "Look at how your own tool calls have actually gone: which tools you "
         "used, how often each failed or was refused, and what the operator "
         "approved or rejected. Use this when reflecting on your own "
         "performance — it is evidence, where your memory of a conversation is "
         "not, and it survives restarts. Returns counts only, never past "
         "content. Record what you conclude with propose_memory so the lesson "
         "outlives this conversation; the operator reviews it before it "
         "becomes durable.",
     "inputSchema": schema_object({
         "days": {"type": "integer", "minimum": 1, "maximum": 90, "default": 7,
                  "description": "How far back to look. Default 7."}})},
]


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


def dispatch(name, args):
    if name == "propose_memory":
        return bridge_request("POST", "/v1/proposals", args)
    if name == "list_memory_proposals":
        return bridge_request("GET", f"/v1/proposals?limit={int(args.get('limit', 50))}")
    if name == "review_own_activity":
        return bridge_request("GET", f"/v1/activity?days={int(args.get('days', 7))}")
    if name == "search_memories":
        return bridge_request("GET", f"/v1/memories?limit={int(args.get('limit', 50))}")
    raise ToolError(f"unknown tool: {name}")


class MemoryMcp(McpHandler):
    service_name = "memory-mcp"
    instructions = ("Use for reviewed personal memory. Propose sensitive memory; "
                    "storing it durably requires operator approval.")
    tools = TOOLS
    dispatch = staticmethod(dispatch)
    bridge_url = BRIDGE_URL
    shared_token = os.environ.get("MEMORY_MCP_SHARED_TOKEN", "")


if __name__ == "__main__":
    serve(MemoryMcp)
