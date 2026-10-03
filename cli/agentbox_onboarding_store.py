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

# How long a settled request is kept. It holds no secret, only who approved
# what and how it went. It is kept so an operator can see who let a person in,
# months later.
SETTLED_RETENTION = 90 * 24 * 3600


def reap(now: int | None = None) -> tuple[int, int]:
    """Remove settled requests past their window, and dead invites.

    These are removed for different reasons. A settled request is history and
    ages out. An invite that expired unused still holds its secret. It can no
    longer be used, so the secret is deleted.

    A submitted invite stays however old, because it still waits on an admin.
    A completed one has already dropped its secret and records someone joining
    the household, so it stays permanently.

    It never raises, so an unreadable file is skipped and does not stop the
    portal starting.
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
