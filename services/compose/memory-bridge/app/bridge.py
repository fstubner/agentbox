#!/usr/bin/env python3
"""The memory bridge. Holds memories, proposals and the feedback backlog in one
JSON file behind a process-wide lock, so there is a single writer."""
from __future__ import annotations

import hmac
import json
import os
import re
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
# The assistant may propose a memory. It may not approve one or write straight
# to durable memory. Those need a second credential, the review token, which the
# assistant never holds. Proposing and accepting your own change is not review,
# which is also why merge_own_pr is always_denied.
#
# With MEMORY_REVIEW_TOKEN unset nobody can approve, so durable memory stops
# accepting writes rather than accepting them from anyone with the bridge token.
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


# Raised whenever a field is added that older records will not have. The
# upgrade below runs on load, so an old record never quietly takes a new field's
# default. Without it, proposals written before `kind` existed read as "not
# feedback" and were approved into memory when they were feedback.
STORE_VERSION = 1


def upgrade_store(store: dict[str, Any]) -> dict[str, Any]:
    """Bring a store written by an older version up to date. Idempotent."""
    version = int(store.get("version", 0))
    if version >= STORE_VERSION:
        store["version"] = STORE_VERSION
        return store
    if version < 1:
        # v0 to v1: classify records that predate `kind` rather than default
        # them, as approve_proposal does.
        for item in list(store.get("proposals", [])) + list(store.get("memories", [])):
            if not item.get("kind"):
                kind, reason = classify_kind(item.get("statement", ""))
                item["kind"] = kind
                item["kind_reason"] = reason
                item["kind_source"] = "auto-at-upgrade"
    store["version"] = STORE_VERSION
    return store


def load_store() -> dict[str, Any]:
    if not MEMORY_PATH.exists():
        return {"memories": [], "proposals": [], "version": STORE_VERSION}
    return upgrade_store(json.loads(MEMORY_PATH.read_text(encoding="utf-8")))


def save_store(store: dict[str, Any]) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MEMORY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MEMORY_PATH)


HOUSEHOLD = "household"


def identity_of(handler) -> str:
    """Who this request is for, as stated by agentbox-mcp, never by the model.

    Empty means single-operator mode, where every memory belongs to the one
    operator.
    """
    return (handler.headers.get("X-Agentbox-Identity", "").strip()
            if handler is not None else "")


def resolve_scope(body: dict[str, Any], identity: str) -> str:
    """Private to the caller, or shared with the household.

    Two scopes rather than sharing between named people. Per-item sharing makes
    "what can Sam see?" impossible to answer without reading every row. Putting
    something in household is a deliberate act with one obvious meaning.
    """
    requested = str(body.get("scope") or "").strip().lower()
    if not requested:
        # Private by default. Sharing something because nobody said otherwise
        # is a disclosure nobody chose.
        return identity or HOUSEHOLD
    if requested == HOUSEHOLD:
        return HOUSEHOLD
    if requested in ("private", "me", "self"):
        return identity or HOUSEHOLD
    if identity and requested != identity:
        # Writing into someone else's private scope is impersonation, not
        # sharing.
        raise BridgeError(
            403, f"cannot write to '{requested}'s private memory. Use scope "
                 f"'{HOUSEHOLD}' to share, or omit scope to keep it yours.")
    return requested


# The operator's review path. The review token means "the person who runs this
# box", and they must see every proposal because they are the only one who can
# approve them. This takes no privacy away, since the operator can read the
# memory file with one `docker exec` anyway. What a scope controls is what the
# assistant shows to whom: Sam's assistant cannot read Alex's private memories.
def visible_scopes(identity: str, operator: bool = False) -> set[str] | None:
    """Scopes readable here. None means unrestricted (operator review)."""
    if operator:
        return None
    return {identity, HOUSEHOLD} if identity else {HOUSEHOLD}


# --- memory and feedback ---------------------------------------------------
#
# Two different things arrive through one door. "Sam is allergic to peanuts" is
# a fact and belongs in memory. "Stop asking me to confirm every calendar read"
# is a complaint about behaviour. Storing that as a memory patches around the
# problem and spends a line of context every session, so it goes to a backlog
# to be fixed in the skill, tool description or prompt instead.
#
# The classification is a heuristic and may be wrong. The reviewer sees which
# way it went and why, and can flip it.

KIND_MEMORY = "memory"
KIND_FEEDBACK = "feedback"

# Phrases about the assistant's conduct rather than the world. Second person
# plus a directive is the main signal, since a fact about a person rarely
# addresses the reader.
FEEDBACK_MARKERS = (
    "you should", "you shouldn't", "you should not", "you must", "you need to",
    "you keep", "you always", "you never", "you tend to", "you often",
    "don't ask", "do not ask", "stop asking", "stop doing", "stop telling",
    "instead of asking", "rather than asking", "prefer that you",
    "i'd prefer you", "i would prefer you", "please don't", "please do not",
    "remember to ask", "make sure you", "be more", "be less",
    "too verbose", "too long", "too many questions", "annoying",
    "asked you", "told you", "keeps happening", "every time i ask",
    "when i ask you", "you got it wrong", "you were wrong", "that was wrong",
)


# Words that only appear when the assistant is describing its own machinery,
# such as a note that a tool keeps failing. In practice that is the most common
# kind of feedback, and the fix is to repair the tool rather than remember that
# it is broken.
SELF_REPORT_MARKERS = (
    "upstream_rejected", "approval_required", "always_denied",
    "was refused", "were refused", "refused ", "% success", "0/",
    "calls returned", "returned upstream", "this tool", "the tool",
    "tool description", "requires ", "required argument",
    "do not retry", "do not silently", "do not propose", "don't propose",
    "before proposing", "before calling", "before i ", "always call",
    "always run", "when in doubt", "my memory proposals", "my calls",
    "i need a grant", "ask the operator to grant",
)

# A tool name, snake_case with at least one underscore. Household facts do not
# mention find_or_create_task, so a proposal that does is about the machine.
TOOL_NAME = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def classify_kind(statement: str) -> tuple[str, str]:
    """Guess whether this is a fact to remember or feedback to act on.

    Returns (kind, reason). The reason is shown to the reviewer so the guess can
    be corrected with confidence. The signals, most certain first, are someone
    addressing the assistant's conduct, the assistant reporting on its own
    machinery, and a bare directive.
    """
    text = " " + statement.lower().strip() + " "
    for marker in FEEDBACK_MARKERS:
        if marker in text:
            return KIND_FEEDBACK, f"sounds like feedback about behaviour (“{marker.strip()}”)"
    for marker in SELF_REPORT_MARKERS:
        if marker in text:
            return KIND_FEEDBACK, (f"describes how a tool behaved "
                                   f"(“{marker.strip()}”) — worth fixing, not remembering")
    match = TOOL_NAME.search(statement.lower())
    if match:
        return KIND_FEEDBACK, (f"names a tool (“{match.group(0)}”), so it is "
                               f"probably about the system rather than the household")
    # A bare instruction to the assistant, such as "always confirm before…" or
    # "never read my email out loud".
    first = text.strip().split(" ")[0] if text.strip() else ""
    if first in ("always", "never", "stop", "don't", "dont", "avoid"):
        return KIND_FEEDBACK, f"starts with a directive (“{first}”)"
    return KIND_MEMORY, ""


def resolve_kind(body: dict[str, Any], statement: str) -> tuple[str, str, str]:
    """(kind, reason, source). An explicit kind always beats the guess."""
    requested = str(body.get("kind") or "").strip().lower()
    if requested in (KIND_MEMORY, KIND_FEEDBACK):
        return requested, str(body.get("kind_reason") or ""), "explicit"
    kind, reason = classify_kind(statement)
    return kind, reason, "auto"


def clean_memory(body: dict[str, Any], status: str,
                 identity: str = "") -> dict[str, Any]:
    statement = str(body.get("statement", "")).strip()
    if not statement:
        raise BridgeError(400, "statement is required")
    kind, kind_reason, kind_source = resolve_kind(body, statement)
    return {
        "scope": resolve_scope(body, identity),
        "id": body.get("id") or str(uuid.uuid4()),
        "type": body.get("type", "profile_preference"),
        "statement": statement,
        # Which kind this is, why, and whether a person said so or the
        # heuristic guessed. A classification is only reviewable with all three.
        "kind": kind,
        "kind_reason": kind_reason,
        "kind_source": kind_source,
        "source": body.get("source", ""),
        "confidence": body.get("confidence", "medium"),
        "sensitivity": body.get("sensitivity", "medium"),
        "status": status,
        "created_at": body.get("created_at") or now(),
        "updated_at": now(),
        # The memory this replaces, if any. Links live on the items so any
        # one of them can find the whole history.
        "supersedes": body.get("supersedes") or None,
        "metadata": body.get("metadata", {}),
    }


def is_operator(handler) -> bool:
    """Whether this request carries the operator's review token.

    The same token approves proposals, so seeing the queue and acting on it are
    one permission.
    """
    if handler is None or not REVIEW_TOKEN:
        return False
    provided = handler.headers.get(REVIEW_HEADER, "")
    return bool(provided) and hmac.compare_digest(provided, REVIEW_TOKEN)


def visible_to(items: list[dict[str, Any]], identity: str,
               operator: bool = False) -> list[dict[str, Any]]:
    """Drop anything outside this identity's scopes.

    Done here, in the process holding the store, rather than trusted to the
    caller. Items written before scopes existed have none and read as
    household.
    """
    scopes = visible_scopes(identity, operator)
    if scopes is None:
        return list(items)
    return [i for i in items if i.get("scope", HOUSEHOLD) in scopes]


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
    """Who this session is for and what it can read.

    Answered from the same header that the filter uses, so it cannot disagree
    with what is enforced.
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
    operator = is_operator(handler)
    items = visible_to(filter_items(store["proposals"], {}),
                       identity_of(handler), operator)
    return 200, {"proposals": items[:limit], "total": len(items),
                 "as_operator": operator}


def create_proposal(handler, body):
    item = clean_memory(body or {}, "proposed", identity_of(handler))
    with _LOCK:
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
    with _LOCK:
        store = load_store()
        replaced = None
        if item.get("supersedes"):
            replaced = _mark_superseded(store, item["supersedes"], item["id"])
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
    with _LOCK:
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
                            if (prior := _prior_versions(by_id, x)) else {}))
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
    with _LOCK:
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
            return _file_as_feedback(store, proposal)
        proposal["status"] = STATUS_APPROVED
        proposal["updated_at"] = now()
        replaced = None
        supersedes = str((body or {}).get("supersedes") or
                         proposal.get("supersedes") or "")
        if supersedes:
            replaced = _mark_superseded(store, supersedes, proposal["id"])
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


def _file_as_feedback(store: dict[str, Any], proposal: dict[str, Any]):
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


def _mark_superseded(store: dict[str, Any], old_id: str, new_id: str) -> dict:
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
    with _LOCK:
        store = load_store()
        new = next((x for x in store.get("memories", [])
                    if x.get("id") == new_id), None)
        if not new:
            raise BridgeError(404, "memory not found")
        if new.get("status", STATUS_APPROVED) != STATUS_APPROVED:
            raise BridgeError(409, f"{new_id} is {new.get('status')}, so it "
                                   f"cannot be the current version of anything")
        replaced = _mark_superseded(store, old_id, new_id)
        new["supersedes"] = old_id
        new["updated_at"] = now()
        save_store(store)
    return 200, {**new, "replaced": {"id": replaced["id"],
                                     "statement": replaced["statement"]}}


MAX_PRIOR_VERSIONS = int(os.environ.get("MEMORY_MAX_PRIOR_VERSIONS", "3"))


def _prior_versions(by_id: dict[str, Any], item: dict[str, Any]) -> list[dict]:
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
    with _LOCK:
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
    with _LOCK:
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


def list_feedback(handler, body):
    """The feedback backlog. Operator only, and never assistant context, so
    the assistant cannot start apologising for things instead of them being
    fixed."""
    require_review(handler)
    query = query_of(handler)
    wanted = first(query, "status", "open")
    with _LOCK:
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
    with _LOCK:
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


def reject_proposal(handler, proposal_id: str, body):
    """Decline a proposal. Without it the queue only grows."""
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
# Reads the outcome journals agentbox-mcp writes and returns an aggregate, so
# reflection works from a record rather than from the model's recollection of a
# conversation. It returns counts and rates, never journal lines. "archive_gmail
# was refused six times" is something to act on, and a replay of six refusals
# is not.

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
        raise BridgeError(400, "days must be an integer") from None

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

    # Each tool's tier, because "denied" alone is ambiguous. Without it, a
    # refused approval_required tool looks like one that is never allowed, and
    # reflection concludes it should stop asking.
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
            elif tiers[name] == "allowed" and entry.get("denied"):
                # A refused `allowed` tool is not broken. The bridge is saying
                # something is not set up, such as an entity not on the
                # allowlist or a protected file. Said explicitly so reflection
                # does not conclude the tool is broken.
                entry["note"] = ("permitted, but the bridge refused: usually "
                                 "not configured (an entity not on the "
                                 "operator's allowlist, a guarded path) rather "
                                 "than broken. Ask what to add, do not "
                                 "conclude it is unusable")
        summary[name] = entry

    # Error classes, not messages, because an upstream message quotes its input.
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
        # Said plainly, so an empty window does not read as evidence of good
        # behaviour.
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
