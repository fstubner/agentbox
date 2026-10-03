"""The portal's calls to the memory bridge, with scope checks before every decision.

Loaded by cli/agentbox-portal, never imported on its own. Other portal
names are reached through `portal`, the portal module itself.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# The portal that loaded this file, which registers it as "<portal>.<part>".
portal = sys.modules[__name__.rpartition(".")[0]]

__all__ = [
    "memory_call",
    "BridgeUnreachable",
    "own_proposals",
    "stored_memories",
    "forget_memory",
    "decide_memory",
]


# --- memory bridge -------------------------------------------------------------

def memory_call(method: str, path: str, payload: dict | None = None) -> dict | None:
    """Call memory-bridge with the review credential.

    That token can approve anything, so scope is enforced here, before the
    call, in decide_memory. The bridge cannot tell one portal user from
    another.
    """
    token = os.environ.get("MEMORY_BRIDGE_TOKEN", "")
    review = os.environ.get("MEMORY_REVIEW_TOKEN", "")
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if review:
        headers["X-Memory-Review-Token"] = review
    data = json.dumps(payload).encode() if payload is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(portal.MEMORY_BRIDGE_URL + path, data=data,
                                     method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None

class BridgeUnreachable(Exception):
    """The memory service did not answer.

    A distinct condition from an empty queue, and it has to stay distinct. The
    defect this portal was built to fix was a queue that rendered as no rows;
    reporting "nothing waiting" when the service is down reproduces exactly
    that failure one layer up.
    """

def own_proposals(identity: str, role: str) -> list[dict]:
    """Proposals this session may act on.

    A member sees their own. An admin also sees household proposals, because
    someone has to decide those, but not another member's private ones.
    """
    payload = portal.memory_call("GET", "/v1/proposals?limit=200")
    if payload is None:
        raise BridgeUnreachable
    allowed = {identity}
    if portal.can(role, "memory:decide_household"):
        allowed.add("household")
    return [p for p in payload.get("proposals", [])
            if p.get("scope", "household") in allowed]

def stored_memories(identity: str, role: str) -> tuple[list[dict], dict]:
    """Current memories this session may see, and the history behind each.

    One call including superseded rows rather than a history request per
    memory: the chain is derivable from the rows themselves, and N+1 requests
    to render a page is how a list of ten becomes slow enough that nobody
    opens it.
    """
    payload = portal.memory_call("GET", "/v1/memories?limit=200&include_superseded=true")
    if payload is None:
        raise BridgeUnreachable
    allowed = {identity}
    if portal.can(role, "memory:decide_household"):
        allowed.add("household")
    rows = [m for m in payload.get("memories", [])
            if m.get("scope", "household") in allowed]
    by_id = {m.get("id"): m for m in rows}
    current = [m for m in rows if m.get("status", "approved") == "approved"]
    history: dict[str, list[dict]] = {}
    for item in current:
        chain, cursor = [], item.get("supersedes")
        while cursor in by_id and cursor not in {c.get("id") for c in chain}:
            chain.append(by_id[cursor])
            cursor = by_id[cursor].get("supersedes")
        history[item["id"]] = chain
    return current, history

def forget_memory(identity: str, role: str, memory_id: str,
                  origin: str = portal.ORIGIN_EMAIL) -> tuple[bool, str]:
    """Retire a stored memory, refusing anything out of scope.

    Withheld from assistant-minted sessions for the same reason approving is:
    editing what it may remember by deleting the inconvenient parts is the
    same capability as writing memory, in reverse.
    """
    portal.require(role, "memory:decide_own", origin)
    try:
        current, _ = stored_memories(identity, role)
    except BridgeUnreachable:
        return False, "memory_service_down"
    if memory_id not in {m.get("id") for m in current}:
        return False, "not_yours"
    result = portal.memory_call("POST", f"/v1/memories/{memory_id}/forget",
                         {"reason": "removed from the portal"})
    if result is None:
        return False, "memory_service_down"
    return True, "memory_forgotten"

def decide_memory(identity: str, role: str, proposal_id: str, verb: str,
                  origin: str = portal.ORIGIN_EMAIL,
                  statement: str = "") -> tuple[bool, str]:
    """Approve, file as feedback, or reject one proposal.

    The scope check re-reads the proposal rather than trusting the form. An id
    from a crafted form must never decide whose memory is touched.

    The edited statement does come from the form, because it is what the
    person typed, and it only applies to a proposal they may decide.
    """
    portal.require(role, "memory:decide_own", origin)
    try:
        visible = {p.get("id"): p for p in portal.own_proposals(identity, role)}
    except BridgeUnreachable:
        return False, "memory_service_down"
    if proposal_id not in visible:
        return False, "not_yours"

    payload: dict = {}
    edited = statement.strip()[:2000]
    if edited and edited != str(visible[proposal_id].get("statement", "")).strip():
        payload["statement"] = edited
    if verb == "feedback":
        # Force the classification, then approve: the bridge routes anything
        # marked feedback to the backlog rather than to memory, so one path
        # covers both and they cannot disagree about where it lands.
        payload["kind"] = "feedback"
        route = "approve"
    elif verb == "approve":
        payload["kind"] = "memory"
        route = "approve"
    else:
        route = "reject"

    result = portal.memory_call("POST", f"/v1/proposals/{proposal_id}/{route}", payload)
    if result is None:
        return False, "memory_service_down"
    if verb == "feedback":
        return True, "filed_as_feedback"
    if verb == "approve":
        return True, ("memory_saved_edited" if "statement" in payload
                      else "memory_saved")
    return True, "memory_rejected"
