"""Invite records on disk, checking them, and the Google consent link an invite can carry."""
from __future__ import annotations

import hmac
import json
import os
import time
import urllib.parse
from pathlib import Path

STATE = Path(os.environ.get(
    "AGENTBOX_INVITE_DIR",
    str(Path("~/.local/state/agentbox/invites").expanduser())))
DEFAULT_TTL_HOURS = int(os.environ.get("AGENTBOX_INVITE_TTL_HOURS", "24"))
# A client id is public. The secret stays on the operator side, so this page
# can start Google's consent but not finish it. It receives an authorisation
# code, which is useless without the secret. That is why onboarding has two
# halves.
GOOGLE_CLIENT_ID = os.environ.get("AGENTBOX_GOOGLE_CLIENT_ID", "").strip()
# Must stay in step with google-workspace-bridge/oauth-setup.py. A member
# onboarded with fewer scopes than the bridge calls gets a token that fails at
# the first Drive request, days later, with an opaque 403.
GOOGLE_SCOPES = os.environ.get(
    "AGENTBOX_GOOGLE_SCOPES",
    "https://www.googleapis.com/auth/gmail.modify "
    "https://www.googleapis.com/auth/calendar "
    "https://www.googleapis.com/auth/drive.file "
    "https://www.googleapis.com/auth/drive.activity.readonly "
    "https://www.googleapis.com/auth/contacts.readonly")

PUBLIC_ORIGIN = os.environ.get("AGENTBOX_INVITE_ORIGIN", "").rstrip("/")

# What a person can have their own account for. Shared services, such as the
# one Home Assistant, are stated rather than offered.
CONNECTORS = {
    "vikunja": {
        "label": "Tasks and lists",
        "personal": True,
        "note": "Your own task list. Shared projects (shopping, household) are "
                "shared inside the app afterwards.",
        "external": False,
    },
    "google": {
        "label": "Gmail and Calendar",
        "personal": True,
        "note": "You sign in with Google. Agentbox never sees your password — "
                "only a token you can revoke from your Google account.",
        "external": True,
    },
    "homeassistant": {
        "label": "The house",
        "personal": False,
        "note": "One home, shared by everyone. Nothing to set up.",
        "external": False,
    },
    "memory": {
        "label": "What the assistant remembers about you",
        "personal": False,
        "note": "Private to you by default. Nothing to set up.",
        "external": False,
    },
}


def now() -> int:
    return int(time.time())


def google_auth_url(token_id: str, secret: str) -> str:
    """Where to send the person for consent.

    `prompt=consent` and `access_type=offline` together make Google return a
    refresh token. Without them, someone who approved this client before gets
    none, and the bridge cannot be set up.
    """
    redirect = f"{PUBLIC_ORIGIN}/google/callback"
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        # Carries the invite through Google and back, so the callback knows
        # whose onboarding this is without a cookie or a session.
        "state": f"{token_id}:{secret}",
    })


def invite_path(token_id: str) -> Path:
    return STATE / f"{token_id}.json"


def load_invite(token_id: str) -> dict | None:
    try:
        return json.loads(invite_path(token_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_invite(record: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    path = invite_path(record["id"])
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    # The secret is in here. Nobody but the operator reads this directory.
    path.chmod(0o600)


def valid_invite(token_id: str, secret: str) -> tuple[dict | None, str]:
    """Return (record, reason). A refusal never says which part was wrong."""
    record = load_invite(token_id)
    generic = "This invite link is not valid. Ask for a new one."
    if not record:
        return None, generic
    if not hmac.compare_digest(str(record.get("secret", "")), secret):
        return None, generic
    if record.get("used_at"):
        # Not an error. They already did what was asked. The confirmation page
        # is the POST response, so a refresh or a second tap on the link lands
        # here.
        return None, ("You have already filled this in \u2014 thank you. "
                      "Nothing more is needed from you. Whoever runs this box "
                      "finishes the setup, and you will be able to sign in "
                      "once they have.")
    if now() > int(record.get("expires_at", 0)):
        return None, "This invite has expired. Ask for a new one."
    return record, ""
