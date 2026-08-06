#!/usr/bin/env python3
"""Memory bridge on the shared bridge_base. Stores memories/proposals in a
JSON file behind a process-wide lock (single-writer semantics)."""
from __future__ import annotations

import hmac
import json
import os
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

from bridge_base import BridgeError, BridgeHandler, resolve_limit, serve

try:
    import policy_gate
except ImportError:  # no policy mounted: the summary omits tiers rather than fails
    policy_gate = None  # type: ignore[assignment]

MEMORY_PATH = Path(os.environ.get("MEMORY_PATH", "/data/memory.json"))
_LOCK = threading.Lock()

# --- the review gate --------------------------------------------------------
#
# The assistant may PROPOSE a memory. It may not approve one, and it may not
# write straight to durable memory. Those are operator actions, gated by a
# second credential the assistant never holds — the bridge token alone is not
# enough. This is the same reasoning that puts merge_own_pr in always_denied:
# proposing and accepting your own change is not review.
#
# Fails closed. With MEMORY_REVIEW_TOKEN unset nobody can approve, which is the
# safe direction: durable memory stops accepting writes rather than silently
# accepting them from anyone holding the bridge token.
REVIEW_TOKEN = os.environ.get("MEMORY_REVIEW_TOKEN", "")
REVIEW_HEADER = "X-Memory-Review-Token"


def require_review(handler) -> None:
    if not REVIEW_TOKEN:
        raise BridgeError(503, "memory review token is not configured; "
                               "approval is disabled until an operator sets MEMORY_REVIEW_TOKEN")
    provided = handler.headers.get(REVIEW_HEADER, "")
    if not hmac.compare_digest(provided, REVIEW_TOKEN):
        raise BridgeError(403, "memory approval requires the operator review token")


def query_of(handler) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)


def first(query, key, default):
    value = query.get(key, [default])
    return value[0] if value else default


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_store() -> dict[str, Any]:
    if not MEMORY_PATH.exists():
        return {"memories": [], "proposals": []}
    return json.loads(MEMORY_PATH.read_text(encoding="utf-8"))


def save_store(store: dict[str, Any]) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MEMORY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MEMORY_PATH)


HOUSEHOLD = "household"


def identity_of(handler) -> str:
    """Who this request is for. Asserted by the gateway, never by the model.

    Empty means single-operator, where every memory is implicitly the one
    person's — the behaviour that predates identities.
    """
    return (handler.headers.get("X-Agentbox-Identity", "").strip()
            if handler is not None else "")


def resolve_scope(body: dict[str, Any], identity: str) -> str:
    """Private to the caller, or shared with the household.

    Two scopes rather than arbitrary sharing between named people. "Sam can see
    this one thing of Alex's" is a per-item ACL, and per-item ACLs are how
    sharing systems become impossible to reason about — the operator ends up
    unable to answer "what can she see?" without reading every row. Household
    is a plane, not a permission: putting something there is a deliberate act
    with one obvious meaning.
    """
    requested = str(body.get("scope") or "").strip().lower()
    if not requested:
        # Private by default. A memory that lands in the shared plane because
        # nobody said otherwise is a disclosure nobody chose.
        return identity or HOUSEHOLD
    if requested == HOUSEHOLD:
        return HOUSEHOLD
    if requested in ("private", "me", "self"):
        return identity or HOUSEHOLD
    if identity and requested != identity:
        # Writing into somebody else's private plane is not sharing, it is
        # impersonation.
        raise BridgeError(
            403, f"cannot write to '{requested}'s private memory. Use scope "
                 f"'{HOUSEHOLD}' to share, or omit scope to keep it yours.")
    return requested


def visible_scopes(identity: str) -> set[str]:
    """What this identity may read: their own plane plus the household one."""
    return {identity, HOUSEHOLD} if identity else {HOUSEHOLD}


def clean_memory(body: dict[str, Any], status: str,
                 identity: str = "") -> dict[str, Any]:
    statement = str(body.get("statement", "")).strip()
    if not statement:
        raise BridgeError(400, "statement is required")
    return {
        "scope": resolve_scope(body, identity),
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


def visible_to(items: list[dict[str, Any]], identity: str) -> list[dict[str, Any]]:
    """Drop anything outside this identity's planes.

    Filtered here, in the process holding the store, rather than by the caller.
    A gateway that asked politely for only its own memories would leak the
    moment anything upstream got confused about who it was serving.

    Items written before scopes existed have none. They belong to the person
    who was the only user at the time, so they read as household rather than
    vanishing — losing them silently would be worse than over-sharing between
    two people who already share a house.
    """
    return [i for i in items
            if i.get("scope", HOUSEHOLD) in visible_scopes(identity)]


def filter_items(items: list[dict[str, Any]], query: dict[str, Any]) -> list[dict[str, Any]]:
    result = items
    for field in ("type", "status", "sensitivity"):
        wanted = query.get(field)
        if wanted:
            result = [x for x in result if x.get(field) == wanted]
    return result


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


def whoami(handler, body):
    """What this session is, in the terms the assistant needs to be careful.

    Answered by the bridge from the gateway's asserted header rather than by
    the gateway from its own state, so the answer comes from the same place
    that enforces it. A whoami that could disagree with the filter would be
    worse than none — the assistant would trust it.
    """
    identity = identity_of(handler)
    return 200, {
        "identity": identity or None,
        "mode": "multi-identity" if identity else "single-operator",
        "memory_scopes_readable": sorted(visible_scopes(identity)),
        "memory_scope_default": identity or HOUSEHOLD,
        "note": ("You are acting for "
                 f"'{identity}'. Memories you propose are private to them "
                 f"unless you pass scope 'household'. You cannot read another "
                 f"person's private memories, and you cannot act as anyone "
                 f"else — this is fixed by the credential this session holds, "
                 f"not by anything you can say."
                 if identity else
                 "Single-operator: no identities are configured, so everything "
                 "belongs to the one operator."),
    }


def list_proposals(handler, body):
    limit = resolve_limit(first(query_of(handler), "limit", ""), default=50, maximum=200)
    with _LOCK:
        store = load_store()
    items = visible_to(filter_items(store["proposals"], {}), identity_of(handler))
    return 200, {"proposals": items[:limit], "total": len(items)}


def create_proposal(handler, body):
    item = clean_memory(body or {}, "proposed", identity_of(handler))
    with _LOCK:
        store = load_store()
        # Idempotent on the statement: a retried proposal must not queue the
        # same fact twice for review. Nothing here identifies a proposal except
        # what it says, so that is the key.
        existing = next((x for x in store["proposals"]
                         if x.get("statement", "").strip() == item["statement"]), None)
        if existing:
            return 200, existing
        store["proposals"].append(item)
        save_store(store)
    return 201, item


def create_memory(handler, body):
    """Write straight to durable memory, bypassing the proposal queue.

    Operator-only: this is the path that makes the queue optional, so it takes
    the review token like approval does.
    """
    require_review(handler)
    item = clean_memory(body or {}, "approved", identity_of(handler))
    with _LOCK:
        store = load_store()
        store["memories"].append(item)
        save_store(store)
    return 201, item


def list_memories(handler, body):
    limit = resolve_limit(first(query_of(handler), "limit", ""), default=50, maximum=200)
    with _LOCK:
        store = load_store()
    identity = identity_of(handler)
    items = visible_to(filter_items(store["memories"], {}), identity)
    return 200, {"memories": items[:limit], "total": len(items),
                 "scopes_visible": sorted(visible_scopes(identity))}


def approve_proposal(handler, proposal_id: str):
    require_review(handler)
    with _LOCK:
        store = load_store()
        proposal = next((x for x in store["proposals"] if x.get("id") == proposal_id), None)
        if not proposal:
            raise BridgeError(404, "proposal not found")
        proposal["status"] = "approved"
        proposal["updated_at"] = now()
        store["memories"].append(proposal)
        store["proposals"] = [x for x in store["proposals"] if x.get("id") != proposal_id]
        save_store(store)
    return 200, proposal


def reject_proposal(handler, proposal_id: str, body):
    """Decline a proposal. Without this the queue only ever grows — the live
    store had a proposal sitting unreviewed for six weeks because there was no
    way to say no."""
    require_review(handler)
    with _LOCK:
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


# --- self-reflection --------------------------------------------------------
#
# The assistant can propose memories but had no way to know how it had been
# doing — so any "reflection" was the model recalling a conversation, which is
# the least reliable evidence available and does not survive a restart.
#
# This reads the outcome journals the MCPs write and returns an aggregate. It
# lives in the bridge, not the MCP, for the usual reason: the MCP is a gate,
# the bridge does the work and enforces the capability authoritatively.
#
# It returns *counts and rates*, never journal lines. The journal already omits
# argument values, but an aggregate is also the useful shape: "archive_gmail
# was refused six times" is actionable, and a replay of six refusals is not.

LOG_DIR = Path(os.environ.get("BRIDGE_LOG_DIR", "/logs"))


def read_outcomes(since_days: int) -> list[dict[str, Any]]:
    cutoff = time.time() - since_days * 86400
    records: list[dict[str, Any]] = []
    if not LOG_DIR.is_dir():
        return records
    for path in sorted(LOG_DIR.glob("*-outcomes.jsonl")):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue  # a torn final line is normal for an append log
                if record.get("ts", 0) >= cutoff:
                    records.append(record)
        except OSError:
            continue
    return records


def activity(handler, body):
    """Aggregate what the assistant has tried, and how it went."""
    query = query_of(handler)
    try:
        days = max(1, min(90, int(first(query, "days", "7"))))
    except ValueError:
        raise BridgeError(400, "days must be an integer")

    records = read_outcomes(days)
    calls = [r for r in records if r.get("tool")]
    decisions = [r for r in records if r.get("action")]

    tools: dict[str, dict[str, Any]] = {}
    for record in calls:
        name = record["tool"]
        entry = tools.setdefault(name, {"calls": 0, "ok": 0, "error": 0,
                                        "denied": 0, "invalid": 0, "ms": []})
        entry["calls"] += 1
        outcome = record.get("outcome", "")
        if outcome in entry:
            entry[outcome] += 1
        if isinstance(record.get("ms"), (int, float)):
            entry["ms"].append(record["ms"])

    # Tier per tool, because "denied" alone is ambiguous in a way that produced
    # a wrong conclusion on the first real run: the assistant saw archive_gmail
    # refused, recorded "do not retry archive_gmail", and was mistaken —
    # archive_gmail is approval_required and available with a grant, not
    # always_denied. Without the tier there is no way to tell "ask for this"
    # from "never do this", and the safe-looking reading is the wrong one.
    tiers = {}
    if policy_gate is not None:
        try:
            loaded = policy_gate.load_tiers()
            tool_map = policy_gate.load_tool_map()
            for name in tools:
                capability = tool_map.get(name)
                for tier in ("always_denied", "approval_required", "allowed"):
                    if capability in loaded.get(tier, []):
                        tiers[name] = tier
                        break
                else:
                    tiers[name] = "approval_required"  # unmapped fails closed
        except Exception:  # noqa: BLE001 — a summary must not fail on policy
            tiers = {}

    summary = {}
    for name, entry in sorted(tools.items()):
        durations = sorted(entry.pop("ms"))
        if durations:
            entry["median_ms"] = durations[len(durations) // 2]
        if name in tiers:
            entry["tier"] = tiers[name]
            if tiers[name] == "approval_required":
                entry["note"] = "refused without a grant; the operator can approve it"
            elif tiers[name] == "always_denied":
                entry["note"] = "never permitted; do not ask"
        summary[name] = entry

    # Error classes, not messages: an upstream message quotes its input.
    problems: dict[str, int] = {}
    for record in calls:
        if record.get("outcome") in ("error", "invalid") and record.get("detail"):
            key = f"{record['tool']}: {record['detail']}"
            problems[key] = problems.get(key, 0) + 1

    verdicts: dict[str, dict[str, int]] = {}
    for record in decisions:
        subject = record.get("subject", "")
        entry = verdicts.setdefault(subject, {})
        action = record.get("action", "")
        entry[action] = entry.get(action, 0) + 1

    return 200, {
        "window_days": days,
        "total_calls": len(calls),
        "tools": summary,
        "problems": dict(sorted(problems.items(), key=lambda kv: -kv[1])[:20]),
        "operator_decisions": verdicts,
        # Said plainly because a model reading this will otherwise treat an
        # empty window as evidence of good behaviour rather than of no data.
        "note": ("No activity recorded in this window."
                 if not calls else
                 "Counts only. Journal lines are never returned."),
    }


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
        ("GET", "/v1/activity"): activity,
    }

    def capability_for(self, method: str, path: str,
                       body: dict[str, Any] | None) -> str | None:
        # Reading its own activity is what inspect_service_logs was always for:
        # already `allowed` in the policy, never reachable until now.
        if path.startswith("/v1/whoami"):
            # Knowing who you are is not a capability worth gating; being
            # unsure is what causes the mistakes this tool prevents.
            return None
        if path.startswith("/v1/activity"):
            return "inspect_service_logs"
        return None

    def route_fallback(self, method: str, path: str, body):
        prefix = "/v1/proposals/"
        if method == "POST" and path.startswith(prefix):
            for suffix, handler in (("/approve", approve_proposal), ("/reject", reject_proposal)):
                if path.endswith(suffix):
                    proposal_id = path[len(prefix):-len(suffix)]
                    if handler is reject_proposal:
                        return handler(self, proposal_id, body)
                    return handler(self, proposal_id)
        raise BridgeError(404, "not found")


if __name__ == "__main__":
    serve(MemoryBridge)
