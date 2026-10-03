"""How facts change: supersession chains, history and forgetting."""
from __future__ import annotations

import os
import re
from typing import Any

from bridge_base import BridgeError
from memory_store import STORE_LOCK, load_store, now, require_review, save_store

# --- supersession -----------------------------------------------------------
#
# Facts change, and deleting the old one loses the shape of the change. Bin day
# was Tuesday and is now Wednesday, and the useful record is that this replaced
# that on this date. So there are three end states.
#
#   approved    current, and read by the assistant
#   superseded  was true until something replaced it, readable as history
#   forgotten   should never have been stored
#
# Only `approved` is returned as current. Two contradictory facts with nothing
# saying which is current would produce confident wrong answers.

STATUS_APPROVED = "approved"
STATUS_SUPERSEDED = "superseded"
STATUS_FORGOTTEN = "forgotten"

_STOPWORDS = frozenset((
    "the", "a", "an", "is", "are", "was", "were", "be", "on", "in", "at", "to",
    "of", "for", "and", "or", "it", "this", "that", "usually", "typically",
    "his", "her", "their", "my", "our", "goes", "go", "out", "day", "does"))


def _ordered_content_words(statement: str) -> list[str]:
    words = re.findall(r"[a-z0-9']+", statement.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 2]


def _content_words(statement: str) -> set[str]:
    return set(_ordered_content_words(statement))


def _same_subject(a: str, b: str) -> bool:
    """Do two statements start with the same word?

    A cheap stand-in for "are these about the same thing". "Bin day is
    Tuesday" and "Bin day is Wednesday" share only one content word, because
    the words that differ are the point, but statements about the same subject
    nearly always start with it.
    """
    first_a = _ordered_content_words(a)[:1]
    first_b = _ordered_content_words(b)[:1]
    return bool(first_a) and first_a == first_b


def supersession_candidates(store: dict[str, Any], statement: str,
                            scope: str, exclude: str = "") -> list[dict]:
    """Existing memories this statement might replace.

    Suggested, never applied. A wrong automatic replacement would hide a true
    memory behind a false one without saying so. The signal is crude and only
    has to put the right candidate in front of a person.
    """
    words = _content_words(statement)
    if not words:
        return []
    found = []
    for item in store.get("memories", []):
        if item.get("status", STATUS_APPROVED) != STATUS_APPROVED:
            continue
        if item.get("id") == exclude or item.get("scope") != scope:
            continue
        overlap = words & _content_words(item.get("statement", ""))
        if len(overlap) >= 2 or (overlap and
                                 _same_subject(statement, item.get("statement", ""))):
            found.append({"id": item["id"], "statement": item["statement"],
                          "shared_words": sorted(overlap)})
    return found


def mark_superseded(store: dict[str, Any], old_id: str, new_id: str) -> dict:
    """Retire one memory in favour of another. Caller holds the lock."""
    old = next((x for x in store.get("memories", [])
                if x.get("id") == old_id), None)
    if not old:
        raise BridgeError(404, f"cannot supersede {old_id}: no such memory")
    if old.get("status", STATUS_APPROVED) != STATUS_APPROVED:
        raise BridgeError(409, f"{old_id} is already {old.get('status')}")
    old["status"] = STATUS_SUPERSEDED
    old["superseded_by"] = new_id
    old["superseded_at"] = now()
    old["updated_at"] = now()
    return old


def link_supersession(handler, new_id: str, body):
    """Record that a stored memory replaced an older one.

    The suggestion arrives after the write, so two related memories can be
    stored unlinked. This links them without forgetting either.
    """
    require_review(handler)
    old_id = str((body or {}).get("supersedes") or "").strip()
    if not old_id:
        raise BridgeError(400, "supersedes is required")
    if old_id == new_id:
        raise BridgeError(400, "a memory cannot supersede itself")
    with STORE_LOCK:
        store = load_store()
        new = next((x for x in store.get("memories", [])
                    if x.get("id") == new_id), None)
        if not new:
            raise BridgeError(404, "memory not found")
        if new.get("status", STATUS_APPROVED) != STATUS_APPROVED:
            raise BridgeError(409, f"{new_id} is {new.get('status')}, so it "
                                   f"cannot be the current version of anything")
        replaced = mark_superseded(store, old_id, new_id)
        new["supersedes"] = old_id
        new["updated_at"] = now()
        save_store(store)
    return 200, {**new, "replaced": {"id": replaced["id"],
                                     "statement": replaced["statement"]}}


MAX_PRIOR_VERSIONS = int(os.environ.get("MEMORY_MAX_PRIOR_VERSIONS", "3"))


def prior_versions(by_id: dict[str, Any], item: dict[str, Any]) -> list[dict]:
    """The versions this fact replaced, newest first, capped.

    Takes an index built by the caller rather than caching one on the store,
    where it could end up saved to disk. Follows `supersedes` links, so a fact
    revised three times costs three lookups.
    """
    prior, cursor, guard = [], item.get("supersedes"), set()
    while cursor in by_id and cursor not in guard and \
            len(prior) < MAX_PRIOR_VERSIONS:
        guard.add(cursor)
        old = by_id[cursor]
        prior.append({"statement": old.get("statement"),
                      # When it stopped being true, which is what makes the
                      # current one unambiguous.
                      "until": old.get("superseded_at") or old.get("updated_at")})
        cursor = old.get("supersedes")
    return prior


def memory_history(handler, memory_id: str, body):
    """The whole chain this memory belongs to, oldest first.

    Works from any link, because the id someone has is usually from an old
    answer rather than the newest one.
    """
    with STORE_LOCK:
        store = load_store()
    by_id = {x.get("id"): x for x in store.get("memories", [])
             + store.get("forgotten", [])}
    if memory_id not in by_id:
        raise BridgeError(404, "memory not found")
    # Walk back to the oldest, then forward. Each direction needs its own
    # cycle guard. With a shared one, the forward walk would refuse to cross
    # ancestors the backward walk had visited, and any link but the oldest
    # would return a chain of one.
    root = by_id[memory_id]
    walked_back = {root["id"]}
    while root.get("supersedes") in by_id and \
            root["supersedes"] not in walked_back:
        root = by_id[root["supersedes"]]
        walked_back.add(root["id"])
    chain = [root]
    walked_forward = {root["id"]}
    while chain[-1].get("superseded_by") in by_id and \
            chain[-1]["superseded_by"] not in walked_forward:
        chain.append(by_id[chain[-1]["superseded_by"]])
        walked_forward.add(chain[-1]["id"])
    return 200, {
        "history": [{"id": x.get("id"), "statement": x.get("statement"),
                     "status": x.get("status", STATUS_APPROVED),
                     "created_at": x.get("created_at"),
                     "superseded_at": x.get("superseded_at"),
                     "scope": x.get("scope")} for x in chain],
        "current": next((x.get("id") for x in chain
                         if x.get("status", STATUS_APPROVED) == STATUS_APPROVED),
                        None),
        "length": len(chain),
    }


def forget_memory(handler, memory_id: str, body):
    """Remove a memory that is already stored. Operator only.

    Stored memories are read back to the assistant as true, so a wrong one
    misinforms every answer that touches it. It is moved to a `forgotten` list
    the assistant cannot read rather than deleted, which keeps a record of what
    was once believed.
    """
    require_review(handler)
    with STORE_LOCK:
        store = load_store()
        item = next((x for x in store.get("memories", [])
                     if x.get("id") == memory_id), None)
        if not item:
            raise BridgeError(404, "memory not found")
        item["status"] = STATUS_FORGOTTEN
        item["updated_at"] = now()
        reason = str((body or {}).get("reason", ""))[:500]
        if reason:
            item["forgotten_reason"] = reason
        store["memories"] = [x for x in store["memories"]
                             if x.get("id") != memory_id]
        store.setdefault("forgotten", []).append(item)
        save_store(store)
    return 200, item
