"""Who a request is for, and which memories that person may see."""
from __future__ import annotations

from typing import Any

from bridge_base import BridgeError

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
