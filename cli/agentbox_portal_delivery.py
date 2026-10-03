"""Getting sign-in links and invites to people, by email or Discord.

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
    "send_link_email",
    "_smtp_send",
    "send_invite_email",
    "delivery_channels",
    "has_delivery_channel",
    "deliver_link",
    "discord_identities",
    "INVITE_PORT",
    "invite_public_host",
    "deliver_invite",
    "INVITE_DIR",
    "submitted_invites",
]


# --- delivery ------------------------------------------------------------------

def send_link_email(address: str, url: str) -> tuple[bool, str]:
    """Email a login link. Returns (sent, detail).

    Never raises into the request: a delivery failure must not be reported to
    the browser differently from a success, or the page becomes an oracle for
    which addresses are registered.
    """
    host = portal.SETTINGS.value("smtp_host")
    if not host:
        return False, "no SMTP host configured"
    user = portal.SETTINGS.value("smtp_user")
    import email.message
    message = email.message.EmailMessage()
    message["Subject"] = "Your Agentbox sign-in link"
    message["From"] = user or portal.SMTP_FROM
    message["To"] = address
    message.set_content(
        f"Open this link to sign in to Agentbox:\n\n{url}\n\n"
        f"It works once, expires in {portal.LINK_TTL_SECONDS // 60} minutes, and only "
        f"in the browser you requested it from.\n\n"
        f"If you did not ask for this, you can ignore it \u2014 on its own the "
        f"link is not enough to sign in as you.\n")
    return _smtp_send(message)

def _smtp_send(message) -> tuple[bool, str]:
    """Hand a composed message to the configured relay.

    Split out because there are now two kinds of mail with different words in
    them and identical transport. Returns a reason rather than raising, for
    the same reason its callers do: a delivery failure must never be visible
    to the browser as something different from a success.
    """
    import smtplib
    host = portal.SETTINGS.value("smtp_host")
    if not host:
        return False, "no SMTP host configured"
    user = portal.SETTINGS.value("smtp_user")
    try:
        port = int(portal.SETTINGS.value("smtp_port") or 587)
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.starttls()
            if user:
                smtp.login(user, portal.SETTINGS.value("smtp_password"))
            smtp.send_message(message)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}"
    return True, ""

def send_invite_email(address: str, url: str, display_name: str = "") -> tuple[bool, str]:
    """Email an invitation to somebody who does not live here yet.

    Different words from a sign-in link, because it is a different thing: this
    one has no account behind it and asks the recipient to make one.
    """
    import email.message
    user = portal.SETTINGS.value("smtp_user")
    message = email.message.EmailMessage()
    message["Subject"] = "You have been invited to a household Agentbox"
    message["From"] = user or portal.SMTP_FROM
    message["To"] = address
    greeting = f"Hi {display_name},\n\n" if display_name else ""
    message.set_content(
        f"{greeting}You have been invited to join a household assistant "
        f"running on somebody's own hardware.\n\nOpen this to set up your "
        f"account:\n\n{url}\n\nIt works once and expires. Nothing is "
        f"created until the person who runs the box confirms it, so you may "
        f"wait a moment after filling in the form.\n\nIf you were not "
        f"expecting this, ignore it and tell the sender.\n")
    return _smtp_send(message)

# --- delivery ------------------------------------------------------------------
#
# How a sign-in link reaches a person is a household preference, not a security
# property. The link is safe because it is bound to the browser that asked for
# it, so a link in a Discord DM or an inbox is useless to anyone who only reads
# it, including the assistant, which can read both.
#
# That makes Discord delivery safe and useful. It needs no SMTP credential or
# personal sending address, and arrives where the household already talks to
# the assistant.
#
# The portal does not hold the bot token. It writes a delivery request, and
# cli/agentbox-approvals, which runs as the operator, holds the token and
# never passes anything through the model, sends the DM. A page on the LAN
# should not hold a credential that can message the household.

def delivery_channels(identity: str, address: str) -> list[str]:
    """Which channels could carry a link to this person, in order.

    The one definition, used by both `has_delivery_channel` and
    `deliver_link`. If they disagreed, a link judged undeliverable could be
    made with operator privilege and then sent anyway.
    """
    channels = []
    if (portal.SETTINGS.value("smtp_host") or os.environ.get("AGENTBOX_RELAY_URL")) and address:
        channels.append("email")
    if portal.chat_account_for(identity):
        channels.append("discord")
    return channels

def has_delivery_channel(identity: str, address: str) -> bool:
    """Whether an invite for this person would actually travel somewhere.

    The origin depends on the answer and has to be decided before the link is
    minted, because the origin is baked into the record. Transmitted over a
    channel the assistant can read means ORIGIN_CHAT; printed on the admin's
    screen for a human to carry means ORIGIN_OPERATOR, exactly as
    `agentbox-portal link` does.

    Erring is safe in one direction only: if this says yes and delivery then
    fails, the link shown on screen is merely more restricted than it needed
    to be. The reverse would put an operator-privileged link in a mailbox.
    """
    return bool(delivery_channels(identity, address))

def deliver_link(identity: str, address: str, url: str) -> bool:
    """Send a sign-in link by every channel configured. True if any worked.

    Both are attempted rather than one preferred: a household that has set up
    both probably wants the link wherever they are looking, and the failure of
    one must not silently swallow the request.
    """
    delivered = False
    # Derived, not restated. The predicate that chooses the link's origin and
    # the code that actually sends it must agree, or a link minted as
    # hand-over privilege gets transmitted after all.
    channels = delivery_channels(identity, address)

    if "email" in channels:
        sent, detail = portal.send_link_email(address, url)
        if sent:
            delivered = True
        else:
            sys.stderr.write(f"[portal] email to {identity} failed: {detail}\n")

    if "discord" in channels:
        try:
            portal.save_request({
                "id": secrets.token_urlsafe(9),
                "identity": identity,
                "connector": "portal",
                "action": "deliver_link",
                "url": url,
                "created_at": portal.now(),
                "completed_at": None,
            })
            delivered = True
        except OSError as exc:
            sys.stderr.write(f"[portal] could not spool a Discord link: {exc}\n")

    return delivered

def discord_identities() -> set[str]:
    """Identities reachable by Discord DM, paired or configured the old way."""
    out = set(portal.load_chat_links()["linked"])
    for pair in os.environ.get("AGENTBOX_DISCORD_IDENTITIES", "").replace(
            " ", ",").split(","):
        name, _, user = pair.partition(":")
        if name.strip() and user.strip():
            out.add(name.strip())
    return out

INVITE_PORT = int(os.environ.get("AGENTBOX_INVITE_PORT", "8770"))

def invite_public_host() -> str:
    """Where the collecting page is reachable, derived from the portal's own
    public URL rather than configured twice."""
    return urllib.parse.urlparse(portal.PUBLIC_URL).hostname or "agentbox.local"

def deliver_invite(record: dict, url: str) -> list[str]:
    """Send an invitation. Returns the channels that accepted it.

    The person has no identity here yet, so the targets come from the
    proposal an admin approved rather than from an identity lookup.

    The URL lets whoever opens it fill in the form as the named person. That
    only produces a submitted invite, which creates nothing until an admin
    approves it on Operations.
    """
    channels = []
    address = str(record.get("address", ""))
    if address and portal.SETTINGS.value("smtp_host"):
        sent, detail = send_invite_email(
            address, url, str(record.get("display_name", "")))
        if sent:
            channels.append("email")
        else:
            sys.stderr.write(f"[portal] invite email failed: {detail}\n")

    user_id = str(record.get("discord_user_id", ""))
    if user_id:
        # The portal does not hold the bot token. Same two-phase split as
        # every other DM it causes to be sent.
        portal.save_request({
            "id": secrets.token_urlsafe(9),
            "identity": str(record.get("identity", "")),
            "connector": "portal",
            "action": "deliver_invite",
            "discord_user_id": user_id,
            "display_name": str(record.get("display_name", "")),
            "url": url,
            "created_at": portal.now(),
            "completed_at": None,
        })
        channels.append("discord")
    return channels

INVITE_DIR = Path(os.environ.get(
    "AGENTBOX_INVITE_DIR",
    str(Path("~/.local/state/agentbox/invites").expanduser())))

def submitted_invites() -> list[dict]:
    """Invites that were filled in and not yet completed.

    Read only. The portal can show that an onboarding is waiting and ask for
    it to be finished, while `agentbox invite drain` decides and does it.
    The secret is left out, because this renders a page.
    """
    out = []
    try:
        paths = sorted(INVITE_DIR.glob("*.json"))
    except OSError:
        return []
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not record.get("used_at") or record.get("completed_at"):
            continue
        token_id = str(record.get("id", ""))
        if not portal.agentbox_onboarding.TOKEN_ID.match(token_id):
            continue
        out.append({
            "id": token_id,
            "identity": str(record.get("identity", "")),
            "display_name": str(record.get("display_name", "")),
            "connectors": [str(c) for c in record.get("connectors", [])],
            "google_ready": bool(record.get("google_code")),
            "requested": portal.agentbox_onboarding.already_requested(token_id),
        })
    return out
