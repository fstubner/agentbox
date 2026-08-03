#!/usr/bin/env python3
"""Builder MCP — the assistant's view of the repo.

Fronts builder-bridge. The bridge holds the git clone and enforces what may be
changed; this maps that onto tools and the capabilities they exercise.

Every tool description says plainly that proposals are never merged or
deployed. That is not decoration: a model that believes it has shipped a change
will report to the operator that the work is done, and the operator will
believe it.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from mcp_base import McpHandler, ToolError, schema_object, serve

BRIDGE_URL = os.environ.get("BUILDER_BRIDGE_URL", "http://builder-bridge:8080")
BRIDGE_TOKEN = os.environ.get("BUILDER_BRIDGE_TOKEN", "")

FILE_ENTRY = {
    "type": "object",
    "properties": {
        "path": {"type": "string",
                 "description": "Repo-relative path, e.g. "
                                "services/compose/foo-bridge/app/bridge.py"},
        "content": {"type": "string",
                    "description": "The complete new contents of the file. "
                                   "This replaces the file; it is not a patch."},
    },
    "required": ["path", "content"],
    "additionalProperties": False,
}

TOOLS = [
    {"name": "list_repo_files", "title": "List repository files",
     "description": "List tracked files, optionally under a prefix. Also "
                    "returns the paths you are not allowed to change.",
     "inputSchema": schema_object({
         "prefix": {"type": "string",
                    "description": "Limit to a subdirectory, e.g. 'services/'."},
         "limit": {"type": "integer", "minimum": 1, "maximum": 2000,
                   "default": 200}})},

    {"name": "read_repo_file", "title": "Read a repository file",
     "description": "Read one tracked text file. The response says whether the "
                    "file is writable — check that before drafting a change to "
                    "it, rather than discovering the refusal on submit.",
     "inputSchema": schema_object({
         "path": {"type": "string"}}, ["path"])},

    {"name": "run_repo_checks", "title": "Run the repository validators",
     "description": "Run `agentbox validate` against the checked-out tree: "
                    "secret scanning, network-binding rules, compose "
                    "guardrails, and that every assistant tool maps to a "
                    "capability. Run this before proposing a change, and say "
                    "in your proposal that you did.",
     "inputSchema": schema_object({})},

    {"name": "propose_change", "title": "Propose a change for review",
     "description":
         "Write files onto a new branch and push it for the operator to "
         "review. This does NOT merge and does NOT deploy — nothing you "
         "propose takes effect until a human merges it and deploys it, which "
         "you cannot do and must not claim to have done. Use it to add a new "
         "service, fix a bug you found, or improve your own tooling. You "
         "cannot change the approval policy, either policy gate, the operator "
         "CLI, or CI; those are refused. `content` is the whole file, not a "
         "diff, so read the file first when editing an existing one.",
     "inputSchema": schema_object({
         "branch": {"type": "string",
                    "description": "Short branch name, e.g. 'add-todoist-bridge'. "
                                   "Prefixed with proposal/ automatically."},
         "message": {"type": "string",
                     "description": "Commit subject — the first thing the "
                                    "operator reads. Say what changes and why."},
         "rationale": {"type": "string",
                       "description": "Optional longer explanation for the "
                                      "commit body."},
         "files": {"type": "array", "items": FILE_ENTRY, "minItems": 1,
                   "maxItems": 25},
     }, ["branch", "message", "files"])},

    {"name": "list_proposals", "title": "List your open proposals",
     "description": "Branches you have proposed, newest first. Check this "
                    "before proposing again so you do not duplicate one that "
                    "is already waiting for review.",
     "inputSchema": schema_object({})},
]


def bridge_request(method, path, payload=None, query=None):
    if not BRIDGE_TOKEN:
        raise ToolError("BUILDER_BRIDGE_TOKEN is not configured")
    url = f"{BRIDGE_URL}{path}"
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items()
                                             if v not in (None, "")})
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {BRIDGE_TOKEN}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        # Generous: `validate` shells out to docker compose per service and a
        # push over a slow link is not instant.
        with urllib.request.urlopen(request, timeout=660) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:600]
        try:
            detail = json.loads(detail).get("error", detail)
        except ValueError:
            pass
        raise ToolError(detail)
    except urllib.error.URLError as exc:
        raise ToolError(f"builder bridge unreachable: {exc.reason}")


def dispatch(name, args):
    if name == "list_repo_files":
        return bridge_request("GET", "/v1/files",
                              query={"prefix": args.get("prefix", ""),
                                     "limit": args.get("limit", 200)})
    if name == "read_repo_file":
        return bridge_request("GET", "/v1/file", query={"path": args["path"]})
    if name == "run_repo_checks":
        return bridge_request("POST", "/v1/checks", payload={})
    if name == "propose_change":
        return bridge_request("POST", "/v1/proposals", payload={
            "branch": args["branch"], "message": args["message"],
            "rationale": args.get("rationale", ""), "files": args["files"]})
    if name == "list_proposals":
        return bridge_request("GET", "/v1/proposals")
    raise ToolError(f"unknown tool: {name}")


class BuilderMcp(McpHandler):
    service_name = "builder-mcp"
    instructions = ("Read and propose changes to the Agentbox repository. "
                    "Proposals are branches for a human to review — you cannot "
                    "merge or deploy, and must not report that you have.")
    tools = TOOLS
    dispatch = staticmethod(dispatch)
    bridge_url = BRIDGE_URL
    shared_token = os.environ.get("BUILDER_MCP_SHARED_TOKEN", "")


if __name__ == "__main__":
    serve(BuilderMcp)
