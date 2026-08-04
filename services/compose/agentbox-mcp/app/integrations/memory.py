"""Memory MCP: the assistant proposes and reads; it cannot approve or store.

Approval and direct writes are operator actions behind a review token the
assistant never holds — see memory-bridge. Exposing them here once meant the
review gate did not exist, guarded only by a sentence in a tool description.
"""
from __future__ import annotations

from mcp_base import ToolError, schema_object
from integrations._client import bridge_client

bridge_request = bridge_client("MEMORY", "memory-bridge", timeout=20)

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
