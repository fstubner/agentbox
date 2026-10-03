"""The feedback backlog: proposals that describe the assistant's behaviour rather than the world."""
from __future__ import annotations

from typing import Any

from bridge_base import BridgeError
from memory_kinds import KIND_FEEDBACK
from memory_store import STORE_LOCK, first, load_store, now, query_of, require_review, save_store


def file_as_feedback(store: dict[str, Any], proposal: dict[str, Any]):
    """Move a proposal to the feedback backlog. Caller holds the lock.

    Not added to `memories`, so it never becomes context the assistant reads
    back to excuse the behaviour. It stays on the list until a person says it
    is fixed.
    """
    proposal["status"] = "open"
    proposal["kind"] = KIND_FEEDBACK
    proposal["updated_at"] = now()
    store["proposals"] = [x for x in store["proposals"]
                          if x.get("id") != proposal.get("id")]
    store.setdefault("feedback", []).append(proposal)
    save_store(store)
    return 200, proposal


def list_feedback(handler, body):
    """The feedback backlog. Operator only, and never assistant context, so
    the assistant does not apologise for a problem in place of the problem
    being fixed."""
    require_review(handler)
    query = query_of(handler)
    wanted = first(query, "status", "open")
    with STORE_LOCK:
        store = load_store()
    items = store.get("feedback", [])
    if wanted != "all":
        items = [x for x in items if x.get("status", "open") == wanted]
    return 200, {"feedback": items, "total": len(items),
                 "note": "the assistant cannot read these; they are things to "
                         "fix in a skill, prompt or tool description"}


def decide_feedback(handler, feedback_id: str, verb: str, body):
    """Mark one backlog item folded (fixed properly) or dismissed."""
    require_review(handler)
    status = "folded" if verb == "fold" else "dismissed"
    with STORE_LOCK:
        store = load_store()
        item = next((x for x in store.get("feedback", [])
                     if x.get("id") == feedback_id), None)
        if not item:
            raise BridgeError(404, "feedback not found")
        item["status"] = status
        item["updated_at"] = now()
        note = str((body or {}).get("note", ""))[:500]
        if note:
            # What was changed, so a folded item is distinguishable from a
            # dismissed one later.
            item["resolution"] = note
        save_store(store)
    return 200, item
