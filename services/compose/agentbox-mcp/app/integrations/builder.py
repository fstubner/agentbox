"""Builder MCP — the assistant's view of the repo.

Fronts builder-bridge. The bridge holds the git clone and enforces what may be
changed; this maps that onto tools and the capabilities they exercise.

Every tool description says plainly that proposals are never merged or
deployed. That is not decoration: a model that believes it has shipped a change
will report to the operator that the work is done, and the operator will
believe it.
"""
from __future__ import annotations

from mcp_base import ToolError, schema_object
from integrations._client import bridge_client

bridge_request = bridge_client("BUILDER", "builder-bridge", timeout=660)

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
