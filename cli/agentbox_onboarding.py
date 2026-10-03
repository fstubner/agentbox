"""Onboarding approvals: the portal asks, and a privileged worker decides.

Completing an invite creates a Vikunja account, exchanges an OAuth code and
starts a per-person bridge with `docker compose up`. A web page on the LAN
must never hold the Docker socket, so the portal only triggers completion. The
privilege stays with a separate worker.

## Two gates

The portal writes one file naming an invite. It runs no Docker command, holds
no bridge token, and gains nothing, because writing into a directory is a
request, not authority. A separate unit reads the file, re-derives everything
itself, and acts.

The worker trusts one field of what the portal wrote, which invite is meant,
and re-reads that invite to decide everything else. The approver, time and
origin are recorded for auditing and never treated as permission.

## What this limits

A request can only name an invite an operator created and the invitee filled
in. Nothing here can conjure an identity, so the most a compromised portal
could do is complete an onboarding a person had already started.

Approvals expire. A request left in the spool while the worker was down for a
week should not fire when it returns, so `MAX_REQUEST_AGE` is checked against
the approval. Whether the invite itself is still valid stays cli/agentbox's
decision.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from agentbox_onboarding_store import (  # noqa: F401
    SETTLED_RETENTION,
    invite_dir,
    proposals_dir,
    reap,
    settled_dir,
    spool_dir,
)

# Invite ids are `secrets.token_hex(8)`. Anchored and fixed-width, because the
# string comes from an HTTP form and will become a filename.
TOKEN_ID = re.compile(r"^[0-9a-f]{16}$")

# An approval is a decision made at a moment. A week later it is a stale file,
# not a decision, and should be re-made by a person rather than honoured.
MAX_REQUEST_AGE = 24 * 3600


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

    Does not check whether the invite exists or is completable.
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


# --- invitations the assistant drafted -----------------------------------------
#
# The assistant may compose an invitation and choose how it travels. It may not
# decide who lives here, so what it writes is a proposal: it appears on
# Operations and sends nothing until an admin says so.
#
# The threat this shape answers is specific. The assistant reads household
# mail, so a message saying "please add alex@example.com to your assistant" is
# untrusted input that reaches the model. Without the approval step, composing
# that invitation and sending it would be one tool call, and a stranger would
# hold a credential that creates an identity on this box.

IDENTITY_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

INVITE_TTL_HOURS = 48


class NameTaken(ValueError):
    """Proposed for somebody who already lives here."""


class AlreadyInvited(ValueError):
    """An invite for this name is already outstanding."""


def outstanding(identity: str = "", now: int | None = None) -> list[dict]:
    """Invites still live: unused and not expired.

    Each one is a credential, so this is how the number of live links per
    person is limited. Otherwise repeated submissions, or a refresh, would
    each create another valid link.
    """
    now = int(time.time()) if now is None else now
    want = (identity or "").strip().lower()
    out = []
    try:
        paths = sorted(invite_dir().glob("*.json"))
    except OSError:
        return []
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.get("used_at") or record.get("completed_at"):
            continue
        try:
            if int(record.get("expires_at", 0)) <= now:
                continue
        except (TypeError, ValueError):
            continue
        if want and str(record.get("identity", "")).lower() != want:
            continue
        out.append(record)
    return out


def create_invite(identity: str, existing: set[str],
                  ttl_hours: int = INVITE_TTL_HOURS) -> dict:
    """Create an invite record. Unprivileged, as it writes one file.

    `existing` is required so no caller can forget it. Completing an invite
    for a name already in use would rebuild that person's bridge with whoever
    filled in the form and hand them the account. There is no good reason to
    invite someone to a taken name, so this refuses.
    """
    identity = (identity or "").strip().lower()
    if not IDENTITY_NAME.match(identity):
        raise ValueError(
            "a name must be lowercase letters, digits, dashes or underscores, "
            "starting with a letter, for example sam")
    if identity in {e.strip().lower() for e in existing}:
        raise NameTaken(f"{identity} already lives here")
    # Checked here rather than at the call site, for the same reason `existing`
    # is a required argument: a second live invite for one person is a second
    # credential, and the caller that forgets is the one rendering a page where
    # a refresh repeats the request.
    if outstanding(identity):
        raise AlreadyInvited(f"{identity} already has an invite waiting")

    import secrets as _secrets
    record = {
        "id": _secrets.token_hex(8),
        "secret": _secrets.token_urlsafe(32),
        "identity": identity,
        "created_at": int(time.time()),
        "expires_at": int(time.time()) + ttl_hours * 3600,
        "used_at": None,
    }
    directory = invite_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record['id']}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    # The secret is in here, and it is what makes the link a credential.
    path.chmod(0o600)
    return record


def invite_url(record: dict, host: str, port: int = 8770) -> str:
    return (f"http://{host}:{port}/?i={record['id']}"
            f"&t={record['secret']}")


def propose(identity: str, display_name: str, address: str = "",
            discord_user_id: str = "", proposed_by: str = "") -> dict:
    """Record an invitation the assistant drafted. Sends nothing.

    It does not check whether the name is free. That check happens where the
    invite is created, and a second copy here could disagree with it.
    """
    import secrets as _secrets
    record = {
        "id": _secrets.token_hex(8),
        "identity": (identity or "").strip().lower(),
        "display_name": (display_name or "").strip()[:80],
        "address": (address or "").strip()[:200],
        "discord_user_id": (discord_user_id or "").strip()[:32],
        "proposed_by": proposed_by,
        "proposed_at": int(time.time()),
    }
    directory = proposals_dir()
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / f"{record['id']}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    path.chmod(0o600)
    return record


def proposals() -> list[dict]:
    """Drafted invitations waiting on an admin, oldest first."""
    out = []
    try:
        paths = sorted(proposals_dir().glob("*.json"))
    except OSError:
        return []
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not TOKEN_ID.match(str(record.get("id", ""))):
            continue
        if path.name != f"{record['id']}.json":
            continue
        record["path"] = path
        out.append(record)
    out.sort(key=lambda r: int(r.get("proposed_at", 0)))
    return out


def proposal(proposal_id: str) -> dict | None:
    if not TOKEN_ID.match(proposal_id or ""):
        return None
    for record in proposals():
        if record["id"] == proposal_id:
            return record
    return None


def discard_proposal(record: dict) -> None:
    """Drop a proposal. Not archived: an invitation nobody sent is not an
    event worth keeping, and the assistant may well draft it again."""
    path = record.get("path")
    if isinstance(path, Path):
        try:
            path.unlink()
        except OSError:
            pass
