"""Google credentials and settings, the access-token cache, and the HTTP helpers every route uses."""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from bridge_base import BridgeError
from pii import strip_sensitive

BRIDGE_TOKEN = os.environ.get("GOOGLE_BRIDGE_TOKEN", "")
CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")

# Named once so the call sites fit on a line and read as intent rather than URL.
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
ALLOWED_WRITE_CALENDAR_ID = os.environ.get("GOOGLE_ALLOWED_WRITE_CALENDAR_ID", "")
OWNED_LABEL_PREFIX = os.environ.get("GOOGLE_OWNED_LABEL_PREFIX", "agentbox/")
DRIVE_API = "https://www.googleapis.com/drive/v3"
# Writes land here and nowhere else, the same shape as the calendar rule. Unset
# means Drive writes are unavailable rather than unrestricted.
AGENT_DRIVE_FOLDER_ID = os.environ.get("GOOGLE_AGENT_DRIVE_FOLDER_ID", "")
# Google Docs/Sheets/Slides have no bytes to download; they export. Anything
# not in this map is fetched with alt=media instead.
DRIVE_EXPORT_AS = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
DRIVE_MAX_TEXT = int(os.environ.get("GOOGLE_DRIVE_MAX_TEXT", "40000"))
# Remove card numbers, bank details, national insurance and social security
# numbers, and credential-shaped strings from text before it leaves this
# process. On by default, because the assistant has no use for any of them.
# Set GOOGLE_STRIP_SENSITIVE=0 to pass text through untouched.
STRIP_SENSITIVE = os.environ.get("GOOGLE_STRIP_SENSITIVE", "1").strip() not in (
    "0", "false", "no", "")


def clean_text(text):
    """Apply the irreversible strip, if enabled."""
    return strip_sensitive(text) if STRIP_SENSITIVE else text
MAX_DRIVE_BYTES = int(os.environ.get("GOOGLE_DRIVE_MAX_BYTES", str(4 * 1024 * 1024)))


def require_config():
    missing = [
        name for name, value in {
            "GOOGLE_CLIENT_ID": CLIENT_ID,
            "GOOGLE_CLIENT_SECRET": CLIENT_SECRET,
            "GOOGLE_REFRESH_TOKEN": REFRESH_TOKEN,
            "GOOGLE_BRIDGE_TOKEN": BRIDGE_TOKEN,
        }.items() if not value
    ]
    if missing:
        raise BridgeError(503, {"missing_env": missing})


def post_form(url, data):
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=encoded, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


# The access token, cached until shortly before it expires. Fetching one per
# request roughly doubled the time of a Gmail search. The margin is there
# because the token has to outlive the request it is used for.
_TOKEN_CACHE: dict = {"value": "", "expires_at": 0.0}
_TOKEN_LOCK = threading.Lock()
TOKEN_REFRESH_MARGIN = 120


def access_token():
    require_config()
    with _TOKEN_LOCK:
        # Checked inside the lock, so a burst of calls fetches one token.
        if _TOKEN_CACHE["value"] and time.time() < _TOKEN_CACHE["expires_at"]:
            return _TOKEN_CACHE["value"]
        token = post_form("https://oauth2.googleapis.com/token", {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": REFRESH_TOKEN,
            "grant_type": "refresh_token",
        })
        value = token.get("access_token")
        if not value:
            raise BridgeError(502, "Google token response did not include access_token")
        # Use Google's expires_in, with a short fallback if it is missing.
        lifetime = int(token.get("expires_in", 600) or 600)
        _TOKEN_CACHE["value"] = value
        _TOKEN_CACHE["expires_at"] = time.time() + max(0, lifetime - TOKEN_REFRESH_MARGIN)
        return value


def google_json(method, url, payload=None, raw_body=None, content_type=None):
    """A JSON request, or a pre-encoded body when the API will not take JSON.

    Only Drive's multipart upload needs `raw_body`, because metadata and file
    bytes travel in one request.
    """
    if raw_body is not None:
        data = raw_body
    else:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {access_token()}")
    req.add_header("Accept", "application/json")
    if content_type:
        req.add_header("Content-Type", content_type)
    elif payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw[:500]
        raise BridgeError(exc.code, {"google_error": detail}) from None


def google_bytes(method, url):
    """Fetch raw file bytes rather than a JSON envelope.

    Capped at MAX_DRIVE_BYTES so a large file cannot hit the container's memory
    limit, where an out-of-memory kill would take every Google call down.
    """
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {access_token()}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read(MAX_DRIVE_BYTES + 1)[:MAX_DRIVE_BYTES]
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw[:500]
        raise BridgeError(exc.code, {"google_error": detail}) from None


# There is no delete here on purpose. Deleting mail and files is
# always_denied and has no tool, and code that could do it would still sit in
# the process holding the OAuth credential. Absent is stronger than unused.


def decode_b64url(value):
    if not value:
        return ""
    padded = value + "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")
