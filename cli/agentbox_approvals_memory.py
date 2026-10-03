"""Reviewing memory proposals in Discord: posting them and handling replies."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from agentbox_approvals_discord import discord, dm_channel, log, op_read, short_id

# --- memory review in Discord ---------------------------------------------------
#
# The portal can review proposals, but it needs a browser and a fresh link. A
# memory proposal is a quick decision, so reviewing it in Discord keeps the
# queue short.
#
# The protections of the approval loop apply, because it is the same loop. Bot
# messages are ignored, so the assistant cannot approve its own memory by typing
# the words. Only configured operator ids are accepted, and this process holds
# the review token, which the assistant never sees.
#
# It adds one privacy rule. A memory proposal can be private to one person, so
# household proposals go to the channel and private ones only to that person's
# DM. Without a paired Discord account a private proposal stays in the portal.

MEMORY_BRIDGE_URL = os.environ.get("AGENTBOX_MEMORY_BRIDGE",
                                   "http://127.0.0.1:3471")


def identity_discord_map() -> dict[str, str]:
    """identity -> discord user id, from `alex:123,sam:456`."""
    raw = os.environ.get("AGENTBOX_DISCORD_IDENTITIES", "")
    mapping = {}
    for pair in raw.replace(" ", ",").split(","):
        name, _, user = pair.partition(":")
        if name.strip() and user.strip():
            mapping[name.strip()] = user.strip()
    return mapping


def memory_call(method: str, path: str, payload: dict | None = None):
    """Talk to the memory bridge with the review credential."""
    token = os.environ.get("MEMORY_BRIDGE_TOKEN") or op_read(
        "op://Agentbox/memory-bridge/bridge_token")
    review = os.environ.get("MEMORY_REVIEW_TOKEN") or op_read(
        "op://Agentbox/memory-bridge/review_token")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
               "X-Memory-Review-Token": review}
    data = json.dumps(payload).encode() if payload is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(MEMORY_BRIDGE_URL + path, data=data,
                                     method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        log(f"memory bridge {method} {path} -> {exc.code}")
        return None
    except Exception as exc:  # noqa: BLE001
        log(f"memory bridge unreachable: {type(exc).__name__}")
        return None


def announce_proposals(channel: str, token: str, state: dict) -> None:
    """Post proposals nobody has been shown yet, to the right audience."""
    payload = memory_call("GET", "/v1/proposals?limit=50")
    if payload is None:
        return
    mapping = identity_discord_map()
    seen = state.setdefault("memories_announced", {})
    for item in payload.get("proposals", []):
        key = str(item.get("id"))
        if key in seen:
            continue
        scope = str(item.get("scope", "household"))
        kind = item.get("kind") or "memory"
        target = channel
        if scope != "household":
            user = mapping.get(scope)
            if not user:
                log(f"proposal {short_id(key)} is private to {scope}, who has "
                    f"no Discord id configured, left for the portal")
                seen[key] = int(time.time())
                continue
            target = dm_channel(user, token)
            if not target:
                continue
        verb = ("This reads as **feedback about my behaviour**, not a fact."
                if kind == "feedback" else "Shall I remember this?")
        posted = discord("POST", f"/channels/{target}/messages", token, {
            "content": (f"**Memory proposal** `{short_id(key)}`"
                        f"{'' if scope == 'household' else ' (private to you)'}\n"
                        f"> {str(item.get('statement',''))[:900]}\n"
                        f"{verb}\n"
                        f"Reply `remember {short_id(key)}`, "
                        f"`feedback {short_id(key)}` to file it as something to "
                        f"fix, or `forget {short_id(key)}`.")})
        if posted:
            seen[key] = int(time.time())
            log(f"asked about memory {short_id(key)} ({scope}/{kind})")


def resolve_memory(prefix: str) -> tuple[str, str]:
    """Find a proposal or a stored memory by id prefix.

    Returns (id, where), where `where` is "proposal" or "memory", or ("", "").
    An ambiguous prefix finds nothing rather than the first match, so a short
    prefix cannot act on the wrong memory.
    """
    hits = []
    for where, path in (("proposal", "/v1/proposals?limit=200"),
                        ("memory", "/v1/memories?limit=200")):
        payload = memory_call("GET", path) or {}
        for item in payload.get(where + "s", []):
            if str(item.get("id", "")).startswith(prefix):
                hits.append((str(item["id"]), where))
    if len(hits) != 1:
        return "", ""
    return hits[0]


def handle_memory_reply(verb: str, prefix: str, channel: str, token: str,
                        state: dict, rest: list[str] | None = None) -> bool:
    """Act on a memory command. True if it was ours to handle.

        memories                       what is stored
        remember <id>                  approve a proposal
        remember <id> replaces <old>   approve it, retiring what it replaces
        replaces <new> <old>           link two that are already stored
        feedback <id>                  file it as something to fix
        forget <id>                    drop a proposal, or retire a memory
    """
    if verb not in ("remember", "forget", "feedback", "memories", "replaces"):
        return False
    rest = rest or []
    if verb == "memories":
        payload = memory_call("GET", "/v1/memories?limit=25") or {}
        items = payload.get("memories", [])
        lines = "\n".join(f"`{short_id(m.get('id'))}` [{m.get('scope')}] "
                          f"{str(m.get('statement',''))[:150]}" for m in items)
        discord("POST", f"/channels/{channel}/messages", token, {
            "content": (f"**What I remember** ({len(items)})\n{lines}\n"
                        f"`forget <id>` to remove one."
                        if items else "I have no memories stored.")})
        return True

    memory_id, where = resolve_memory(prefix)
    if not memory_id:
        discord("POST", f"/channels/{channel}/messages", token,
                {"content": f"no single match for `{prefix}`. Check `memories`"})
        return True

    # `replaces <new> <old>` links two memories that are already stored. This
    # is the common case, because the suggestion appears after the write.
    if verb == "replaces":
        old_id, _ = resolve_memory(rest[0]) if rest else ("", "")
        if not old_id:
            discord("POST", f"/channels/{channel}/messages", token, {
                "content": "say which one it replaces: `replaces <new> <old>`"})
            return True
        result = memory_call("POST", f"/v1/memories/{memory_id}/supersede",
                             {"supersedes": old_id})
        discord("POST", f"/channels/{channel}/messages", token, {
            "content": (f"linked: “{result['replaced']['statement'][:80]}” is "
                        f"now history" if result else "could not link those")})
        return True

    # `remember <id> replaces <old>` does it in one step, before the new fact
    # is ever current alongside the old one.
    supersedes = ""
    if verb == "remember" and len(rest) >= 2 and rest[0].lower() in (
            "replaces", "replacing", "supersedes"):
        supersedes, _ = resolve_memory(rest[1].strip("`"))
        if not supersedes:
            discord("POST", f"/channels/{channel}/messages", token, {
                "content": f"no single match for `{rest[1]}`"})
            return True

    if verb == "forget" and where == "memory":
        result = memory_call("POST", f"/v1/memories/{memory_id}/forget",
                             {"reason": "forgotten from Discord"})
        reply = "forgotten" if result else "could not forget that"
    elif verb == "forget":
        result = memory_call("POST", f"/v1/proposals/{memory_id}/reject",
                             {"reason": "rejected from Discord"})
        reply = "dropped" if result else "could not drop that"
    elif where != "proposal":
        reply = "that is already stored; `forget` is the only thing left to do"
    else:
        payload = {"kind": "feedback" if verb == "feedback" else "memory"}
        if supersedes:
            payload["supersedes"] = supersedes
        result = memory_call("POST", f"/v1/proposals/{memory_id}/approve",
                             payload)
        if not result:
            reply = "could not do that"
        elif verb == "feedback":
            reply = ("filed as feedback: on the list to fix properly, not "
                     "stored as a memory")
        elif result.get("replaced"):
            reply = (f"remembered: “{result['replaced']['statement'][:70]}” "
                     f"is now history")
        else:
            reply = "remembered"
            # The suggestion only exists after the write. Say what to type to
            # act on it, so two contradictory facts do not both stay current.
            for candidate in result.get("possibly_supersedes", [])[:2]:
                reply += (f"\nthis may replace “{candidate['statement'][:60]}” "
                          f"Reply `replaces {short_id(memory_id)} "
                          f"{short_id(candidate['id'])}` to link them")
    discord("POST", f"/channels/{channel}/messages", token, {"content": reply})
    state.get("memories_announced", {}).pop(memory_id, None)
    log(f"{verb} {short_id(memory_id)} -> {reply}")
    return True
