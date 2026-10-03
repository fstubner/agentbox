"""The memory store on disk, its lock and schema upgrades, and the operator review gate."""
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

from bridge_base import BridgeError
from memory_kinds import classify_kind, resolve_kind
from memory_scopes import resolve_scope


def store_path() -> Path:
    """Where the store lives. Read on each call, so a test can point it at a
    temporary file."""
    return Path(os.environ.get("MEMORY_PATH", "/data/memory.json"))


STORE_LOCK = threading.Lock()

# --- the review gate --------------------------------------------------------
#
# The assistant may propose a memory. It may not approve one or write straight
# to durable memory. Those need a second credential, the review token, which the
# assistant never holds. Proposing and accepting your own change is not review,
# which is also why merge_own_pr is always_denied.
#
# With MEMORY_REVIEW_TOKEN unset nobody can approve, so durable memory stops
# accepting writes rather than accepting them from anyone with the bridge token.
def review_token() -> str:
    return os.environ.get("MEMORY_REVIEW_TOKEN", "")


REVIEW_HEADER = "X-Memory-Review-Token"


def require_review(handler) -> None:
    token = review_token()
    if not token:
        raise BridgeError(503, "memory review token is not configured; "
                               "approval is disabled until an operator sets MEMORY_REVIEW_TOKEN")
    provided = handler.headers.get(REVIEW_HEADER, "")
    if not hmac.compare_digest(provided, token):
        raise BridgeError(403, "memory approval requires the operator review token")


def query_of(handler) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)


def first(query, key, default):
    value = query.get(key, [default])
    return value[0] if value else default


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# Raised whenever a field is added that older records will not have. The
# upgrade below runs on load, so an old record never silently takes a new
# field's default. Without it, proposals written before `kind` existed would
# read as "not feedback" and could be approved into memory when they are
# feedback.
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
    path = store_path()
    if not path.exists():
        return {"memories": [], "proposals": [], "version": STORE_VERSION}
    return upgrade_store(json.loads(path.read_text(encoding="utf-8")))


def save_store(store: dict[str, Any]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def is_operator(handler) -> bool:
    """Whether this request carries the operator's review token.

    The same token approves proposals, so seeing the queue and acting on it are
    one permission.
    """
    token = review_token()
    if handler is None or not token:
        return False
    provided = handler.headers.get(REVIEW_HEADER, "")
    return bool(provided) and hmac.compare_digest(provided, token)


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
