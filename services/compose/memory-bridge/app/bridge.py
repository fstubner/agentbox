#!/usr/bin/env python3
"""Memory bridge on the shared bridge_base. Stores memories/proposals in a
JSON file behind a process-wide lock (single-writer semantics)."""
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


# The operator's review path. Presenting the review token means "I am the human
# who administers this box", and that person must see every proposal — they are
# the only one who can approve any of them.
#
# This is not a privacy regression, because the privacy was never there: the
# operator can read /data/memory.json with one docker exec, and a scope that
# claimed otherwise would have been theatre. What a scope actually controls is
# **what the assistant can surface to whom** — Sam's assistant cannot read
# Alex's private memories, which is the property that matters and the one that
# holds.
#
# Until this existed, a private proposal was unreachable by the only account
# that could approve it: write-only memory that failed silently, because an
# empty list looks exactly like an empty queue.
def visible_scopes(identity: str, operator: bool = False) -> set[str] | None:
    """Scopes readable here. None means unrestricted (operator review)."""
    if operator:
        return None
    return {identity, HOUSEHOLD} if identity else {HOUSEHOLD}


# --- memory vs feedback -----------------------------------------------------
#
# Two different things arrive through one door. "Sam is allergic to peanuts"
# is a fact about the household and belongs in memory. "Stop asking me to
# confirm before every calendar read" is not a fact — it is a complaint about
# how the assistant behaves, and storing it as a memory is a patch: the
# behaviour stays wrong, and a line of memory is spent every session
# apologising for it. That belongs in a backlog of things to fix properly, in
# the skill or the tool description or the system prompt.
#
# The classification is a heuristic and is allowed to be wrong, because the
# person reviewing the proposal sees the suggestion and can flip it. What it
# must not do is silently decide: the portal always shows which way it went
# and why.

KIND_MEMORY = "memory"
KIND_FEEDBACK = "feedback"

# Phrases that describe the assistant's conduct rather than the world. Second
# person plus a directive is the core signal — a fact about a person almost
# never addresses the reader.
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


# Vocabulary that only appears when the assistant is describing its own
# operation rather than the household's life.
#
# This half was added after reading the real queue on 2026-08-12, where five
# of seven pending proposals were the assistant writing notes to itself about
# broken tooling — "look_at_camera returned upstream_rejected on every call
# this week (3/3, 0% success). Do not retry it" — and the markers above, which
# were tuned for a person complaining ("you keep asking me"), caught none of
# them. That shape is the *dominant* one here, and it is the purest example of
# the thing worth separating: the fix is to repair look_at_camera, not to
# remember forever that it is broken.
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

# A tool name: snake_case with at least one underscore. Household facts do not
# mention find_or_create_task; a proposal that does is describing the machine.
TOOL_NAME = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def classify_kind(statement: str) -> tuple[str, str]:
    """Guess whether this is a fact to remember or feedback to act on.

    Returns (kind, reason). The reason is shown to whoever reviews it, because
    an unexplained classification is one nobody can correct with confidence.

    Three signals, in order of how sure they are: someone addressing the
    assistant's conduct, the assistant reporting on its own machinery, and a
    bare directive. All are heuristics and all are overridable at review.
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
    # A bare imperative aimed at the assistant: "always confirm before…",
    # "never read my email out loud". No subject, starts with the directive.
    first = text.strip().split(" ")[0] if text.strip() else ""
    if first in ("always", "never", "stop", "don't", "dont", "avoid"):
        return KIND_FEEDBACK, f"starts with a directive (“{first}”)"
    return KIND_MEMORY, ""


def resolve_kind(body: dict[str, Any], statement: str) -> tuple[str, str, str]:
    """(kind, reason, source). An explicit kind always wins over the guess."""
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
        # Which of the two things this is, why we think so, and whether a
        # human said or a heuristic guessed. All three travel together: a
        # classification without its provenance cannot be reviewed.
        "kind": kind,
        "kind_reason": kind_reason,
        "kind_source": kind_source,
        "source": body.get("source", ""),
        "confidence": body.get("confidence", "medium"),
        "sensitivity": body.get("sensitivity", "medium"),
        "status": status,
        "created_at": body.get("created_at") or now(),
        "updated_at": now(),
        # Which memory this replaces, if any. The chain lives on the items
        # themselves so any link can find the whole history.
        "supersedes": body.get("supersedes") or None,
        "metadata": body.get("metadata", {}),
    }


def is_operator(handler) -> bool:
    """Whether this request carries the operator's review credential.

    The same token that authorises approving a proposal, so seeing the queue
    and acting on it are one permission rather than two that can drift apart.
    """
    if handler is None or not REVIEW_TOKEN:
        return False
    provided = handler.headers.get(REVIEW_HEADER, "")
    return bool(provided) and hmac.compare_digest(provided, REVIEW_TOKEN)


def visible_to(items: list[dict[str, Any]], identity: str,
               operator: bool = False) -> list[dict[str, Any]]:
    """Drop anything outside this identity's planes.

    Filtered here, in the process holding the store, rather than by the caller.
    A gateway that asked politely for only its own memories would leak the
    moment anything upstream got confused about who it was serving.

    Items written before scopes existed have none. They belong to the person
    who was the only user at the time, so they read as household rather than
    vanishing — losing them silently would be worse than over-sharing between
    two people who already share a house.
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
    operator = is_operator(handler)
    items = visible_to(filter_items(store["proposals"], {}),
                       identity_of(handler), operator)
    return 200, {"proposals": items[:limit], "total": len(items),
                 "as_operator": operator}


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
        # Already on the improvement backlog. The assistant cannot see that
        # list, so left to itself it would re-propose the same complaint every
        # time the behaviour recurred — which is exactly when it is most
        # likely to notice. Swallowed quietly rather than errored: from the
        # assistant's side this is a proposal that has already been made, not
        # a mistake.
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
    """Write straight to durable memory, bypassing the proposal queue.

    Operator-only: this is the path that makes the queue optional, so it takes
    the review token like approval does.
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
        # Only when the caller did not say. Offered, never applied — see
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
    # `include_superseded=true` returns retired rows as top-level items, which
    # is what a management view wants. The assistant gets something better: the
    # current fact with its own history nested underneath.
    #
    # Excluding history outright was the first design and it was wrong. The
    # argument was that two contradictory memories produce a confident wrong
    # answer — true only when nothing says which is current. Nested under the
    # fact that replaced it, with the date it stopped being true, there is no
    # ambiguity left to be confused by, and the assistant can answer "when did
    # that change?" instead of flatly contradicting somebody who remembers the
    # old value.
    #
    # Nested rather than flat for context economy, which is the real cost:
    # a prior version carries a sentence and a date, not a second copy of
    # every field. Capped, because a fact revised fifty times must not become
    # fifty lines in every retrieval.
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
    """Let the reviewer correct a proposal before it becomes durable.

    The assistant's wording is a draft. "Alex doesn't like early meetings" may
    be true only on Mondays, and the choice was previously all-or-nothing:
    accept a slightly wrong memory forever, or reject and lose it. Both are
    bad, and rejecting is the one that quietly loses information.

    The original is kept beside the edit. A memory a human rewrote and one the
    assistant wrote are different evidence about how well it is doing, and
    collapsing them would corrupt the only record of that.
    """
    body = body or {}
    edited = str(body.get("statement") or "").strip()
    if edited and edited != proposal.get("statement"):
        proposal["original_statement"] = proposal.get("statement", "")
        proposal["statement"] = edited
        proposal["edited_by_reviewer"] = True
    scope = str(body.get("scope") or "").strip().lower()
    if scope:
        # Re-resolved rather than assigned, so the reviewer cannot widen a
        # memory into someone else's private plane by typing a name.
        proposal["scope"] = resolve_scope({"scope": scope}, identity)
    kind = str(body.get("kind") or "").strip().lower()
    if kind in (KIND_MEMORY, KIND_FEEDBACK) and kind != proposal.get("kind"):
        proposal["kind"] = kind
        proposal["kind_source"] = "reviewer"
        proposal["kind_reason"] = "set during review"


def approve_proposal(handler, proposal_id: str, body=None):
    """Approve a proposal, optionally with the reviewer's edits applied.

    A proposal marked as feedback is routed to the feedback backlog instead of
    durable memory even here, so "approve" cannot quietly turn a behaviour
    complaint into a memory that patches around it.
    """
    require_review(handler)
    with _LOCK:
        store = load_store()
        proposal = next((x for x in store["proposals"] if x.get("id") == proposal_id), None)
        if not proposal:
            raise BridgeError(404, "proposal not found")
        apply_reviewer_edits(proposal, body, identity_of(handler))
        if not proposal.get("kind"):
            # Written before this field existed, so it carries no
            # classification — and `.get("kind") == KIND_FEEDBACK` quietly read
            # that as "memory", routing every pre-existing proposal into
            # durable memory however plainly it was feedback. Found the first
            # time the feature was used on the real queue: three notes about
            # broken tooling were approved straight into memory, which is the
            # precise outcome the split exists to prevent. Classify on the way
            # through rather than defaulting.
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
    """Move a proposal into the improvement backlog. Caller holds the lock.

    Deliberately not appended to `memories`: the whole point is that this does
    not become a line of context the assistant reads back to excuse the
    behaviour. It is work for a human to do to a skill, and it stays on a list
    until they say they have done it.
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
# Facts change, and "delete the old one" loses the shape of the change. Bin day
# was Tuesday and is now Wednesday; the useful record is not one fact plus a
# tombstone, it is a chain — this replaced that, on this date. That distinction
# matters when an answer from three weeks ago looks wrong: it lets you see what
# was believed at the time rather than only what is believed now.
#
# So three end states, not two:
#
#   approved    current; the assistant reads these
#   superseded  was true, something replaced it; readable as history
#   forgotten   should never have been stored; wrong, or a test fixture
#
# Only `approved` is returned to the assistant. A superseded memory that stayed
# readable would be worse than deleting it — two contradictory facts with no
# marker of which is current is exactly how a confident wrong answer happens.

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
    """Do two statements lead with the same word?

    Cheap stand-in for "are these about the same thing". Two shared content
    words was the original bar and it missed the case this exists for: "Bin
    day is Tuesday" and "Bin day is Wednesday" share exactly one — `bin` —
    because the words that differ are the whole point. Statements about the
    same subject nearly always lead with it.
    """
    first_a = _ordered_content_words(a)[:1]
    first_b = _ordered_content_words(b)[:1]
    return bool(first_a) and first_a == first_b


def supersession_candidates(store: dict[str, Any], statement: str,
                            scope: str, exclude: str = "") -> list[dict]:
    """Existing memories this statement might be replacing.

    Suggested, never applied. An automatic supersession that is wrong hides a
    true memory behind a false one and says nothing, which is strictly worse
    than leaving both visible for a human to reconcile. Word overlap is a crude
    signal and is meant to be — it only has to be good enough to put the right
    candidate in front of someone.
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
    """Record that an already-stored memory replaced an older one.

    The link is usually made when the new fact is written, but not always: the
    suggestion arrives *after* the write, and somebody reviewing a list months
    later is looking at two memories that were never connected. Without this
    the only way to reconcile them is to forget one, which throws away the
    chain that makes the change legible.
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
    """The versions this fact replaced, newest first and capped.

    Takes a prebuilt index rather than caching one on the store: anything
    stashed there is one save_store away from being written to the file on
    disk, and an index of every memory nested inside the memory file is not a
    mistake worth risking to save a dict comprehension.

    Walks the `supersedes` links rather than searching, so a fact revised
    three times costs three lookups, and a whole listing is O(memories).
    """
    prior, cursor, guard = [], item.get("supersedes"), set()
    while cursor in by_id and cursor not in guard and \
            len(prior) < MAX_PRIOR_VERSIONS:
        guard.add(cursor)
        old = by_id[cursor]
        prior.append({"statement": old.get("statement"),
                      # When it stopped being true, which is the field that
                      # makes "which of these is current" unambiguous.
                      "until": old.get("superseded_at") or old.get("updated_at")})
        cursor = old.get("supersedes")
    return prior


def memory_history(handler, memory_id: str, body):
    """The whole chain this memory belongs to, oldest first.

    Reachable from any link, not just the newest: the id somebody has is
    usually the one they saw in an old answer.
    """
    with _LOCK:
        store = load_store()
    by_id = {x.get("id"): x for x in store.get("memories", [])
             + store.get("forgotten", [])}
    if memory_id not in by_id:
        raise BridgeError(404, "memory not found")
    # Walk back to the oldest, then forward, so any link finds the whole chain.
    #
    # Each direction needs its own cycle guard. Sharing one set means the
    # backward walk marks every ancestor as visited and the forward walk then
    # refuses to re-cross them — so anchoring on anything but the oldest link
    # returned a chain of one. The anchor people actually have is the id from
    # an old answer, which is precisely the case that broke.
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
    """Remove a memory that is already durable. Operator-only.

    Memory was append-only: once approved, a fact could be wrong forever with
    no route out. That is worse here than in most stores, because these
    statements are read back into the assistant's context as true — a stale
    "Bin day is Tuesday" does not sit inertly, it actively misinforms every
    answer that touches it.

    Kept rather than deleted, in a `forgotten` list. The assistant cannot read
    it, and it is the only record of what was once believed — which matters
    when working out why an answer three weeks ago was wrong.
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
    """The improvement backlog. Operator-only: this is not assistant context.

    If the assistant could read this it would start apologising for things
    instead of them being fixed, which is the failure mode the split exists to
    prevent.
    """
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
            # What was actually changed. Without it, a folded item is
            # indistinguishable from a forgotten one six months later.
            item["resolution"] = note
        save_store(store)
    return 200, item


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
            elif tiers[name] == "allowed" and entry.get("denied"):
                # An `allowed` tool being refused is not a fault and not a
                # permission problem: the bridge is saying the thing is not
                # set up — a camera nobody added to the allowlist, a file
                # that is guarded. Said explicitly because the alternative
                # reading is "this tool is broken", which the assistant
                # actually reached and proposed to remember forever.
                entry["note"] = ("permitted, but the bridge refused: usually "
                                 "not configured (an entity not on the "
                                 "operator's allowlist, a guarded path) rather "
                                 "than broken. Ask what to add, do not "
                                 "conclude it is unusable")
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
        ("GET", "/v1/feedback"): list_feedback,
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
