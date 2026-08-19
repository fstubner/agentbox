"""Onboarding approvals: the portal asks, a privileged worker decides.

Completing an invite creates a Vikunja account, exchanges an OAuth code, and
runs `docker compose up` for a new per-identity bridge. `cli/agentbox-invite`
put that behind a terminal on purpose, arguing that a LAN-reachable page
holding the docker socket is the worst thing that could run on this box.

The household decided on 2026-08-19 that onboarding should not need a
terminal. That does not overrule the argument — it moves where completion is
*triggered* from without moving where its privilege *lives*.

## The two gates

The portal writes one file naming an invite. It runs no docker command, holds
no bridge token, and gains no capability it did not already have: writing into
a directory is not authority, it is a request. A separate unit reads that
file, re-derives everything for itself, and acts.

The worker therefore trusts exactly one field of what the portal wrote — which
invite is meant — and re-reads that invite from the invite spool to decide
anything else. Approver, timestamp and origin are recorded for the audit trail
and are never consulted as permission. This is the same split the MCP layer
and the bridges already use, where the non-consuming check is advisory and the
consuming one is authoritative.

## What this bounds

A request can only ever name an invite that an operator already created and
that the invitee already filled in. Nothing here can conjure an identity from
an empty spool, so the worst a compromised portal achieves is completing an
onboarding that a human had already set in motion — not inventing one.

Approvals expire. A file that sits in the spool because the worker was down
for a week should not fire when it comes back, so `MAX_REQUEST_AGE` is checked
against the approval, never against the invite: bounding our own staleness
does not change when an invite is valid, which stays `cli/agentbox`'s call.

Stdlib only, like everything else that runs on this host.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

# Invite ids are `secrets.token_hex(8)`. Anchored and fixed-width, because
# this string arrives from an HTTP form and is about to become a filename —
# the same lesson as the portal's link ids, which reached a path unchecked.
TOKEN_ID = re.compile(r"^[0-9a-f]{16}$")

# An approval is a decision made at a moment. A week later it is a stale file,
# not a decision, and should be re-made by a person rather than honoured.
MAX_REQUEST_AGE = 24 * 3600


def spool_dir() -> Path:
    return Path(os.environ.get(
        "AGENTBOX_ONBOARDING_DIR",
        str(Path("~/.local/state/agentbox/onboarding").expanduser())))


def settled_dir() -> Path:
    return spool_dir() / "settled"


def request_path(token_id: str) -> Path:
    """Where a request for this invite lives.

    Raises rather than sanitising: a token id that is not one is a bug or an
    attack, and quietly rewriting it into something safe would hide both.
    """
    if not TOKEN_ID.match(token_id or ""):
        raise ValueError("not an invite id")
    return spool_dir() / f"{token_id}.json"


def request(token_id: str, requested_by: str, origin: str = "") -> Path:
    """Record that an admin asked for this invite to be completed.

    Deliberately does not check whether the invite exists or is completable.
    That judgement belongs to the side holding the privilege; making it here
    too would mean two implementations that can disagree, and the portal's
    copy would be the one nobody re-reads when the rules change.
    """
    path = request_path(token_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    payload = {
        "token_id": token_id,
        # Audit trail only. The worker does not read these to decide.
        "requested_by": requested_by,
        "requested_at": int(time.time()),
        "origin": origin,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    path.chmod(0o600)
    return path


def pending() -> list[dict]:
    """Requests waiting for the worker, oldest first.

    A file that is not readable, not JSON, or does not name a well-formed
    invite is skipped rather than raising: one bad file in a spool directory
    must not stop every other person's onboarding.
    """
    out = []
    try:
        names = sorted(p for p in spool_dir().iterdir() if p.suffix == ".json")
    except OSError:
        return []
    for path in names:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        token_id = str(record.get("token_id", ""))
        # The filename and the field must agree, and both must be well-formed.
        # Trusting the field alone would let a request name one invite and be
        # filed as another.
        if not TOKEN_ID.match(token_id) or path.name != f"{token_id}.json":
            continue
        record["path"] = path
        out.append(record)
    out.sort(key=lambda r: int(r.get("requested_at", 0)))
    return out


def expired(record: dict, now: int | None = None) -> bool:
    now = int(time.time()) if now is None else now
    return now - int(record.get("requested_at", 0)) > MAX_REQUEST_AGE


def settle(record: dict, outcome: str, detail: str = "") -> None:
    """Move a request out of the spool, keeping why it ended that way.

    Settled rather than deleted, so `agentbox invite drain` running twice on a
    failure does not retry forever and an operator can see what happened
    without reading a journal.
    """
    path = record.get("path")
    if not isinstance(path, Path):
        return
    settled = settled_dir()
    settled.mkdir(parents=True, exist_ok=True)
    settled.chmod(0o700)
    record = {k: v for k, v in record.items() if k != "path"}
    record["outcome"] = outcome
    record["detail"] = detail[:500]
    record["settled_at"] = int(time.time())
    target = settled / path.name
    target.write_text(json.dumps(record, indent=2), encoding="utf-8")
    target.chmod(0o600)
    try:
        path.unlink()
    except OSError:
        pass


def already_requested(token_id: str) -> bool:
    try:
        return request_path(token_id).exists()
    except ValueError:
        return False
