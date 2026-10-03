"""Pairing Discord accounts and delivering sign-in links by direct message."""
from __future__ import annotations

import json
import subprocess
import time

from agentbox_approvals_discord import CHAT_LINKS, LINK_CURSOR, PORTAL_DIR, REPO, discord, dm_channel, log
from agentbox_approvals_memory import identity_discord_map


def complete_pairings(token: str) -> None:
    """Finish Discord pairings started in the portal.

    Someone clicks "Connect Discord" on their own page, gets a short code, and
    sends it to the bot as a direct message. The message coming from their
    account proves they own it. Anyone can type a user id into a form, but only
    the account's owner can send from it.
    """
    try:
        data = json.loads(CHAT_LINKS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    pending = {k: v for k, v in data.get("pending", {}).items()
               if int(v.get("expires_at", 0)) > time.time()}
    if not pending:
        return

    # Only unread DMs, and only ones the bot can see because the person opened
    # a conversation with it.
    channels = discord("GET", "/users/@me/channels", token) or []
    for channel in channels:
        messages = discord("GET", f"/channels/{channel.get('id')}/messages?limit=10",
                           token) or []
        for message in messages:
            author = message.get("author") or {}
            # A bot cannot pair an identity to itself.
            if author.get("bot"):
                continue
            words = str(message.get("content", "")).strip().split()
            if len(words) != 2 or words[0].lower() != "link":
                continue
            code = words[1].strip().upper()
            record = pending.get(code)
            if not record:
                continue
            identity = str(record.get("identity", ""))
            data.setdefault("linked", {})[identity] = {
                "user_id": str(author.get("id")),
                "linked_at": int(time.time()),
            }
            data["pending"].pop(code, None)
            try:
                CHAT_LINKS.write_text(json.dumps(data, indent=2), encoding="utf-8")
                CHAT_LINKS.chmod(0o600)
            except OSError as exc:
                log(f"could not record pairing for {identity}: {exc}")
                continue
            discord("POST", f"/channels/{channel.get('id')}/messages", token, {
                "content": (f"Connected. You are **{identity}** on this "
                            f"Agentbox, and sign-in links will come here.")})
            log(f"paired {identity} to a Discord account")
            return


def send_link_on_request(token: str) -> None:
    """A paired person sends `link` by DM and gets a sign-in link back.

    The assistant's `request_signin_link` makes a link that cannot see
    Operations, and `agentbox-portal link` needs a terminal. This lets someone
    sign in from their phone.

    A message from a paired account authenticates the sender, the same check
    that pairing uses.

    The link is ORIGIN_CHAT, never ORIGIN_OPERATOR. It arrives on a channel the
    assistant can read, so it must grant little to anyone who reads it. It can
    view everything, including Operations. It cannot approve a memory, connect
    or disconnect an account, invite anyone or change a setting.
    """
    try:
        data = json.loads(CHAT_LINKS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    linked = data.get("linked", {})
    if not linked:
        return
    by_user = {str(v.get("user_id")): name for name, v in linked.items()}

    channels = discord("GET", "/users/@me/channels", token) or []
    seen = link_cursor()
    for channel in channels:
        messages = discord("GET", f"/channels/{channel.get('id')}/messages?limit=10",
                           token) or []
        if str(channel.get("id")) not in seen:
            # This conversation has not been seen before. Record where it
            # starts and answer nothing, so old `link` messages already there do
            # not all get live links at once.
            newest = max((int(m.get("id", 0)) for m in messages), default=0)
            remember_link_request(str(channel.get("id")), str(newest))
            continue
        for message in messages:
            author = message.get("author") or {}
            if author.get("bot"):
                continue
            if str(message.get("content", "")).strip().lower() != "link":
                continue
            if int(message.get("id", 0)) <= int(link_cursor().get(
                    str(channel.get("id")), 0) or 0):
                continue          # already answered this one
            identity = by_user.get(str(author.get("id")))
            remember_link_request(str(channel.get("id")), str(message.get("id")))
            if not identity:
                discord("POST", f"/channels/{channel.get('id')}/messages", token, {
                    "content": ("This Discord account is not connected to an "
                                "Agentbox identity yet. Connect it from the "
                                "Accounts page of your portal first.")})
                continue
            try:
                out = subprocess.run(
                    [str(REPO / "cli" / "agentbox-portal"), "link", identity,
                     "--origin", "chat"],
                    capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.SubprocessError) as exc:
                log(f"could not mint a link for {identity}: {exc}")
                continue
            url = (out.stdout or "").strip().splitlines()
            if out.returncode != 0 or not url:
                detail = (out.stderr or "").strip().splitlines()
                discord("POST", f"/channels/{channel.get('id')}/messages", token, {
                    "content": detail[-1] if detail else
                               "Could not mint a link just now."})
                continue
            discord("POST", f"/channels/{channel.get('id')}/messages", token, {
                "content": (f"{url[0]}\n\nSingle use, and it expires shortly. "
                            f"It can look at everything \u2014 memories, "
                            f"accounts, Operations \u2014 and cannot change "
                            f"anything, because anyone who can read this "
                            f"message can open it.")})
            log(f"sent {identity} a chat-origin link on request")


def link_cursor() -> dict:
    try:
        return json.loads(LINK_CURSOR.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember_link_request(channel: str, message_id: str) -> None:
    """Record the message answered, so the same `link` DM is not answered on
    every poll."""
    seen = link_cursor()
    seen[channel] = message_id
    try:
        LINK_CURSOR.parent.mkdir(parents=True, exist_ok=True)
        LINK_CURSOR.write_text(json.dumps(seen), encoding="utf-8")
        LINK_CURSOR.chmod(0o600)
    except OSError as exc:
        log(f"could not record the link request cursor: {exc}")


def deliver_pending_links(token: str) -> None:
    """Send sign-in links the portal queued, as a Discord DM.

    The portal makes the link and writes a request, and this process delivers
    it, because the portal faces the LAN and must not hold the bot token, for
    the same reason it cannot exchange an OAuth code.

    Sending over a channel the assistant can read is safe only because the
    link is bound to the browser that asked for it. The portal enforces that
    binding. If the portal stops binding links, this delivery must stop too.
    """
    directory = PORTAL_DIR / "requests"
    if not directory.is_dir():
        return
    mapping = identity_discord_map()
    try:
        linked = json.loads(CHAT_LINKS.read_text(encoding="utf-8"))["linked"]
        mapping = {**mapping, **{k: v["user_id"] for k, v in linked.items()}}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    for entry in sorted(directory.glob("*.json")):
        try:
            record = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        action = record.get("action")
        if action not in ("deliver_link", "deliver_invite"):
            continue
        if record.get("completed_at"):
            continue
        identity = str(record.get("identity", ""))
        if action == "deliver_invite":
            # An invitation goes to someone with no identity here yet, so the
            # recipient comes from the record, put there by the admin who
            # approved the draft, not by the assistant that wrote it.
            user = str(record.get("discord_user_id", ""))
            if not user:
                log(f"invite for {identity} has no Discord id; not delivered")
                continue
        else:
            user = mapping.get(identity)
            if not user:
                log(f"no Discord id for {identity}; sign-in link not delivered")
                continue
        channel = dm_channel(user, token)
        if not channel:
            continue
        if action == "deliver_invite":
            greeting = str(record.get("display_name", "")) or "there"
            content = (
                f"Hi {greeting}, you have been invited to join a household "
                f"Agentbox, an assistant running on somebody's own "
                f"hardware.\n{record.get('url')}\nOpen it to set up your "
                f"account. It works once and expires. Nothing is created "
                f"until the person who runs the box confirms it, so there may "
                f"be a wait after you fill in the form. If you were not "
                f"expecting this, ignore it and tell whoever sent it.")
        else:
            content = (
                f"Here is your Agentbox sign-in link.\n{record.get('url')}\n"
                f"It works once and expires shortly. Opened in the browser you "
                f"asked from, it gives you everything. Opened anywhere else, "
                f"including from this app, you can read your memories and "
                f"accounts but not approve or disconnect anything, because "
                f"anyone who saw this message could be the one opening it. "
                f"That includes me.")
        posted = discord("POST", f"/channels/{channel}/messages", token, {
            "content": content})
        if not posted:
            continue
        # The URL is a credential until it expires, so drop it once it is
        # sent, like a used authorisation code.
        record.pop("url", None)
        record["completed_at"] = int(time.time())
        try:
            entry.write_text(json.dumps(record, indent=2), encoding="utf-8")
        except OSError as exc:
            log(f"delivered {identity}'s link but could not mark it done: {exc}")
        log(f"delivered a sign-in link to {identity} by DM")
