#!/usr/bin/env python3
"""The memory bridge. Holds memories, proposals and the feedback backlog in one
JSON file behind a process-wide lock, so there is a single writer."""
from __future__ import annotations

import os
from typing import Any

from bridge_base import BridgeError, BridgeHandler, resolve_limit, serve
from memory_activity import (  # noqa: F401
    LOG_DIR,
    activity,
    read_outcomes,
)
from memory_feedback import (  # noqa: F401
    decide_feedback,
    file_as_feedback,
    list_feedback,
)
from memory_history import (  # noqa: F401
    MAX_PRIOR_VERSIONS,
    STATUS_APPROVED,
    STATUS_SUPERSEDED,
    _same_subject,
    forget_memory,
    link_supersession,
    mark_superseded,
    memory_history,
    prior_versions,
    supersession_candidates,
)
from memory_kinds import (  # noqa: F401
    KIND_FEEDBACK,
    KIND_MEMORY,
    classify_kind,
    resolve_kind,
)
from memory_scopes import (  # noqa: F401
    HOUSEHOLD,
    filter_items,
    identity_of,
    resolve_scope,
    visible_scopes,
    visible_to,
    whoami,
)
from memory_store import (  # noqa: F401
    REVIEW_HEADER,
    STORE_LOCK,
    clean_memory,
    first,
    is_operator,
    load_store,
    now,
    query_of,
    require_review,
    save_store,
)

# --- routes -----------------------------------------------------------------


def get_schema(handler, body):
    return 200, {"service": "memory-bridge", "tools": [
        "POST /v1/proposals",
        "GET /v1/proposals",
        "GET /v1/memories",
    ], "operator_only": {
        "tools": ["POST /v1/proposals/{id}/approve",
                  "POST /v1/proposals/{id}/reject",
                  "POST /v1/memories"],
        "requires": REVIEW_HEADER,
        "note": "approval and direct writes are operator actions; the assistant "
                "proposes and reads only. Use cli/agentbox memory.",
    }}


def list_proposals(handler, body):
    limit = resolve_limit(first(query_of(handler), "limit", ""), default=50, maximum=200)
    with STORE_LOCK:
        store = load_store()
    operator = is_operator(handler)
    items = visible_to(filter_items(store["proposals"], {}),
                       identity_of(handler), operator)
    return 200, {"proposals": items[:limit], "total": len(items),
                 "as_operator": operator}


def create_proposal(handler, body):
    item = clean_memory(body or {}, "proposed", identity_of(handler))
    with STORE_LOCK:
        store = load_store()
        # Keyed on the statement, so a retried proposal is not queued twice.
        existing = next((x for x in store["proposals"]
                         if x.get("statement", "").strip() == item["statement"]), None)
        if existing:
            return 200, existing
        # Already on the feedback backlog, which the assistant cannot see.
        # Treated as already proposed rather than as an error.
        filed = next((x for x in store.get("feedback", [])
                      if x.get("statement", "").strip() == item["statement"]), None)
        if filed:
            return 200, {"id": filed.get("id"), "status": filed.get("status"),
                         "kind": KIND_FEEDBACK,
                         "note": "already recorded for review; not queued again"}
        store["proposals"].append(item)
        save_store(store)
    return 201, item


def create_memory(handler, body):
    """Write straight to durable memory, skipping the proposal queue.

    Operator only, so it takes the review token like approval does.
    """
    require_review(handler)
    item = clean_memory(body or {}, STATUS_APPROVED, identity_of(handler))
    with STORE_LOCK:
        store = load_store()
        replaced = None
        if item.get("supersedes"):
            replaced = mark_superseded(store, item["supersedes"], item["id"])
        store["memories"].append(item)
        save_store(store)
        # Only when the caller did not say. Suggested, never applied. See
        # supersession_candidates.
        suggestions = ([] if replaced else
                       supersession_candidates(store, item["statement"],
                                               item["scope"], item["id"]))
    result = dict(item)
    if replaced:
        result["replaced"] = {"id": replaced["id"],
                              "statement": replaced["statement"]}
    if suggestions:
        result["possibly_supersedes"] = suggestions
    return 201, result


def list_memories(handler, body):
    limit = resolve_limit(first(query_of(handler), "limit", ""), default=50, maximum=200)
    with STORE_LOCK:
        store = load_store()
    identity = identity_of(handler)
    operator = is_operator(handler)
    query = query_of(handler)
    # `include_superseded=true` lists retired rows as separate items, for a
    # management view. The assistant instead gets each current fact with the
    # versions it replaced nested under it, each with the date it stopped being
    # true. That leaves no doubt about which is current, and lets the assistant
    # answer "when did that change?". An old version costs a sentence and a
    # date, and the number kept is capped.
    include_history = first(query, "include_superseded", "") == "true"
    current = [x for x in store["memories"]
               if include_history
               or x.get("status", STATUS_APPROVED) == STATUS_APPROVED]
    items = visible_to(filter_items(current, {}), identity, operator)
    if not include_history:
        by_id = {x.get("id"): x for x in store["memories"]}
        items = [dict(x, **({"previously": prior}
                            if (prior := prior_versions(by_id, x)) else {}))
                 for x in items]
    scopes = visible_scopes(identity, operator)
    return 200, {"memories": items[:limit], "total": len(items),
                 "scopes_visible": "all" if scopes is None else sorted(scopes)}


def apply_reviewer_edits(proposal: dict[str, Any], body: dict[str, Any] | None,
                         identity: str) -> None:
    """Let the reviewer correct a proposal before it is stored.

    The assistant's wording is a draft, and otherwise the only choices are a
    slightly wrong memory or none. The original is kept beside the edit,
    because a memory a person rewrote is different evidence from one the
    assistant got right.
    """
    body = body or {}
    edited = str(body.get("statement") or "").strip()
    if edited and edited != proposal.get("statement"):
        proposal["original_statement"] = proposal.get("statement", "")
        proposal["statement"] = edited
        proposal["edited_by_reviewer"] = True
    scope = str(body.get("scope") or "").strip().lower()
    if scope:
        # Resolved again rather than taken from the form, so a reviewer cannot
        # move a memory into someone else's private scope by typing a name.
        proposal["scope"] = resolve_scope({"scope": scope}, identity)
    kind = str(body.get("kind") or "").strip().lower()
    if kind in (KIND_MEMORY, KIND_FEEDBACK) and kind != proposal.get("kind"):
        proposal["kind"] = kind
        proposal["kind_source"] = "reviewer"
        proposal["kind_reason"] = "set during review"


def approve_proposal(handler, proposal_id: str, body=None):
    """Approve a proposal, with the reviewer's edits if any.

    A proposal marked as feedback still goes to the feedback backlog, so
    approving cannot turn a behaviour complaint into a memory.
    """
    require_review(handler)
    with STORE_LOCK:
        store = load_store()
        proposal = next((x for x in store["proposals"] if x.get("id") == proposal_id), None)
        if not proposal:
            raise BridgeError(404, "proposal not found")
        apply_reviewer_edits(proposal, body, identity_of(handler))
        if not proposal.get("kind"):
            # Written before this field existed, so classify it now rather
            # than let a missing value read as "memory".
            kind, reason = classify_kind(proposal.get("statement", ""))
            proposal["kind"] = kind
            proposal["kind_reason"] = reason
            proposal["kind_source"] = "auto-at-approval"
        if proposal.get("kind") == KIND_FEEDBACK:
            return file_as_feedback(store, proposal)
        proposal["status"] = STATUS_APPROVED
        proposal["updated_at"] = now()
        replaced = None
        supersedes = str((body or {}).get("supersedes") or
                         proposal.get("supersedes") or "")
        if supersedes:
            replaced = mark_superseded(store, supersedes, proposal["id"])
            proposal["supersedes"] = supersedes
        store["memories"].append(proposal)
        store["proposals"] = [x for x in store["proposals"] if x.get("id") != proposal_id]
        save_store(store)
        suggestions = ([] if replaced else
                       supersession_candidates(store, proposal["statement"],
                                               proposal["scope"], proposal["id"]))
    result = dict(proposal)
    if replaced:
        result["replaced"] = {"id": replaced["id"],
                              "statement": replaced["statement"]}
    if suggestions:
        result["possibly_supersedes"] = suggestions
    return 200, result


def reject_proposal(handler, proposal_id: str, body):
    """Decline a proposal. Without it the queue only grows."""
    require_review(handler)
    with STORE_LOCK:
        store = load_store()
        proposal = next((x for x in store["proposals"] if x.get("id") == proposal_id), None)
        if not proposal:
            raise BridgeError(404, "proposal not found")
        proposal["status"] = "rejected"
        proposal["updated_at"] = now()
        proposal["rejected_reason"] = str((body or {}).get("reason", ""))[:500]
        store["proposals"] = [x for x in store["proposals"] if x.get("id") != proposal_id]
        store.setdefault("rejected", []).append(proposal)
        save_store(store)
    return 200, proposal


class MemoryBridge(BridgeHandler):
    server_version = "memory-bridge/1.0"
    bridge_token = os.environ.get("MEMORY_BRIDGE_TOKEN", "")
    routes = {
        ("GET", "/schema"): get_schema,
        ("GET", "/v1/whoami"): whoami,
        ("GET", "/v1/proposals"): list_proposals,
        ("POST", "/v1/proposals"): create_proposal,
        ("GET", "/v1/memories"): list_memories,
        ("POST", "/v1/memories"): create_memory,
        ("GET", "/v1/feedback"): list_feedback,
        ("GET", "/v1/activity"): activity,
    }

    def capability_for(self, method: str, path: str,
                       body: dict[str, Any] | None) -> str | None:
        # Reading its own activity is what inspect_service_logs is for.
        if path.startswith("/v1/whoami"):
            # Ungated, because being unsure who you are acting for causes the
            # mistakes this tool prevents.
            return None
        if path.startswith("/v1/activity"):
            return "inspect_service_logs"
        return None

    def route_fallback(self, method: str, path: str, body):
        prefix = "/v1/proposals/"
        if method == "POST" and path.startswith(prefix):
            for suffix, handler in (("/approve", approve_proposal),
                                    ("/reject", reject_proposal)):
                if path.endswith(suffix):
                    proposal_id = path[len(prefix):-len(suffix)]
                    return handler(self, proposal_id, body)
        memory_prefix = "/v1/memories/"
        if method == "GET" and path.startswith(memory_prefix) and \
                path.endswith("/history"):
            return memory_history(
                self, path[len(memory_prefix):-len("/history")], body)
        if method == "POST" and path.startswith(memory_prefix) and \
                path.endswith("/supersede"):
            return link_supersession(
                self, path[len(memory_prefix):-len("/supersede")], body)
        if method == "POST" and path.startswith(memory_prefix) and \
                path.endswith("/forget"):
            return forget_memory(self, path[len(memory_prefix):-len("/forget")],
                                 body)
        feedback_prefix = "/v1/feedback/"
        if method == "POST" and path.startswith(feedback_prefix):
            for suffix in ("/fold", "/dismiss"):
                if path.endswith(suffix):
                    return decide_feedback(
                        self, path[len(feedback_prefix):-len(suffix)],
                        suffix.lstrip("/"), body)
        raise BridgeError(404, "not found")


if __name__ == "__main__":
    serve(MemoryBridge)
