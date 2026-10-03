"""Where onboarding records live, and clearing out settled records and invites that expired unused."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def spool_dir() -> Path:
    return Path(os.environ.get(
        "AGENTBOX_ONBOARDING_DIR",
        str(Path("~/.local/state/agentbox/onboarding").expanduser())))


def settled_dir() -> Path:
    return spool_dir() / "settled"


def invite_dir() -> Path:
    return Path(os.environ.get(
        "AGENTBOX_INVITE_DIR",
        str(Path("~/.local/state/agentbox/invites").expanduser())))


def proposals_dir() -> Path:
    return spool_dir() / "proposals"


# --- keeping the spool from growing forever ------------------------------------

# How long a settled request stays readable. It carries no secret — only who
# approved what, and how it turned out — so this is a retention window rather
# than a disclosure fix, and it is generous on purpose: the question it answers
# is "who let this person in", which is worth being able to ask months later.
SETTLED_RETENTION = 90 * 24 * 3600


def reap(now: int | None = None) -> tuple[int, int]:
    """Drop settled requests past their window, and invites that are dead.

    Two different reasons, deliberately not merged.

    A settled request is history, and ages out. An invite that has expired
    without ever being filled in is something else: its secret is still in the
    file, and that secret is what makes the link a credential. Nobody can
    redeem it — `valid_invite` checks the clock — but keeping a credential
    record past the point where it can authorise anything is a disclosure
    surface with nothing left to buy, which is the same reasoning the portal
    reaps spent links under.

    Deliberately conservative about what counts as dead. A submitted invite
    still holds its secret and is waiting on an admin, so it stays however old
    it is. A completed one has had its secret dropped already and is the record
    of somebody joining the household, so it stays permanently.

    Never raises: this runs at startup beside the portal's own reaper, and an
    unreadable file is worth skipping, not worth refusing to serve over.
    """
    now = int(time.time()) if now is None else now
    settled = invites = 0

    try:
        stale = [p for p in settled_dir().glob("*.json")]
    except OSError:
        stale = []
    for path in stale:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            done = int(record.get("settled_at", 0))
        except (OSError, ValueError, TypeError):
            continue
        if done and now - done <= SETTLED_RETENTION:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        settled += 1

    try:
        candidates = [p for p in invite_dir().glob("*.json")]
    except OSError:
        candidates = []
    for path in candidates:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.get("used_at") or record.get("completed_at"):
            continue
        try:
            expired = int(record.get("expires_at", 0)) <= now
        except (TypeError, ValueError):
            continue
        if not expired:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        invites += 1

    return settled, invites
