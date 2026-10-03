"""Connector requests, Google consent, and pairing a chat account from the page.

Loaded by cli/agentbox-portal, never imported on its own. Other portal
names are reached through `portal`, the portal module itself.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# The portal that loaded this file, which registers it as "<portal>.<part>".
portal = sys.modules[__name__.rpartition(".")[0]]

__all__ = [
    "request_path",
    "save_request",
    "pending_requests",
    "google_redirect_acceptable",
    "google_consent_url",
    "connector_status",
    "PAIRING_TTL_SECONDS",
    "chat_links_path",
    "load_chat_links",
    "save_chat_links",
    "start_pairing",
    "chat_account_for",
    "unlink_chat",
]


# --- connectors ----------------------------------------------------------------
#
# Reconnecting is the same two-phase split as onboarding, for the same reason:
# a LAN-reachable page must not hold the credential that mints refresh tokens.
# This half collects consent and writes a request; `agentbox identity
# reconnect` has the secret and is run by a human who chose to.
#
# Why anyone needs this at all: scopes change. Drive, Drive activity and
# contacts were all added after Alex consented, and every one of them returns
# ACCESS_TOKEN_SCOPE_INSUFFICIENT against a token minted before they existed.
# Without a reconnect path the only fix is deleting an identity and starting
# over, which also orphans their memories.

def request_path(request_id: str) -> Path:
    return portal.STATE / "requests" / f"{request_id}.json"

def save_request(record: dict) -> None:
    path = request_path(record["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    # Holds an authorisation code. Short-lived, but still a credential.
    path.chmod(0o600)

def pending_requests(identity: str = "") -> list[dict]:
    directory = portal.STATE / "requests"
    if not directory.is_dir():
        return []
    out = []
    for entry in sorted(directory.glob("*.json")):
        try:
            record = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.get("completed_at"):
            continue
        if identity and record.get("identity") != identity:
            continue
        out.append(record)
    return out

def google_redirect_acceptable(url: str) -> tuple[bool, str]:
    """Whether Google will accept this as a redirect URI.

    Google accepts loopback over http, or a real public domain over https. A
    .local name is refused as "must end with a public top-level domain". Better
    to know now than halfway through someone's consent.
    """
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return False, "no host in the portal URL"
    if host in ("127.0.0.1", "::1", "localhost"):
        if parsed.scheme != "http":
            return False, "loopback redirects must use http, not https"
        return True, ""
    if parsed.scheme != "https":
        return False, (f"{host} is not loopback, so Google requires https "
                       f"(only 127.0.0.1 and localhost may use http)")
    if host.endswith(".local") or "." not in host:
        return False, (f"{host} is not a public domain; Google rejects .local "
                       f"and bare hostnames. Use a loopback URL, or a real "
                       f"name over https such as a Tailscale *.ts.net host")
    return True, ""

def google_consent_url(state: str) -> str:
    """Start Google's consent flow.

    access_type=offline with prompt=consent, because Google only issues a
    refresh token on a forced consent, and a reconnect exists to replace that
    token.
    """
    query = urllib.parse.urlencode({
        "client_id": portal.GOOGLE_CLIENT_ID,
        "redirect_uri": portal.oauth_redirect_uri(),
        "response_type": "code",
        "scope": portal.GOOGLE_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"

def connector_status(identity: str) -> list[dict]:
    """What this person has connected, from configuration on disk.

    Not a live probe: the bridges have no host ports, so nothing
    on this side of the socket can reach them. Whether the *credential* still
    works is a different question, answered by `agentbox doctor`, which can
    reach the containers. Saying "configured" here and meaning "healthy" would
    be the kind of confident-but-wrong status this system keeps having to fix.
    """
    env_dir = Path(os.environ.get(
        "AGENTBOX_ENV_DIR",
        os.path.expanduser("~/.config/agentbox")))
    google_env = env_dir / f"{identity}-google-bridge.env"
    shared = env_dir / "google-workspace-bridge.env"
    connected = google_env.exists() or (
        identity in portal.admin_names() and shared.exists())
    return [{
        "key": "google",
        "name": "Google (Gmail, Calendar, Drive)",
        "connected": connected,
        "detail": "your own account" if google_env.exists() else (
            "the household account" if connected else "not connected"),
    }]

# --- pairing a chat account from the page --------------------------------------
#
# A person connects their own Discord here rather than an operator editing a
# unit file.
#
# The pairing decides where sign-in links are sent, so it lives in the
# portal's state directory, which no container mounts. If the assistant could
# write it, it could send someone's link to itself. The old environment
# variable is still read for older setups.
#
# The person proves the account is theirs by sending a code from it. Anyone can
# type a user id into a form, but only its owner can send from it.

PAIRING_TTL_SECONDS = int(os.environ.get("AGENTBOX_PAIRING_TTL", "600"))

def chat_links_path() -> Path:
    return portal.STATE / "chat-links.json"

def load_chat_links() -> dict:
    try:
        data = json.loads(chat_links_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"linked": {}, "pending": {}}
    data.setdefault("linked", {})
    data.setdefault("pending", {})
    return data

def save_chat_links(data: dict) -> None:
    path = chat_links_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    path.chmod(0o600)

def start_pairing(identity: str) -> str:
    """Mint a short code for this person to send from their chat account."""
    data = load_chat_links()
    # Unambiguous characters only: this gets read off a screen and typed into
    # a phone, and 0/O and 1/I are where that goes wrong.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(6))
    data["pending"] = {k: v for k, v in data["pending"].items()
                       if v.get("identity") != identity
                       and int(v.get("expires_at", 0)) > portal.now()}
    data["pending"][code] = {"identity": identity,
                             "expires_at": portal.now() + PAIRING_TTL_SECONDS}
    save_chat_links(data)
    return code

def chat_account_for(identity: str) -> str:
    """The chat account this person receives links on, if any."""
    linked = load_chat_links()["linked"].get(identity)
    if linked:
        return str(linked.get("user_id", ""))
    # Boxes configured before pairing existed.
    for pair in os.environ.get("AGENTBOX_DISCORD_IDENTITIES", "").replace(
            " ", ",").split(","):
        name, _, user = pair.partition(":")
        if name.strip() == identity and user.strip():
            return user.strip()
    return ""

def unlink_chat(identity: str) -> None:
    data = load_chat_links()
    data["linked"].pop(identity, None)
    save_chat_links(data)
