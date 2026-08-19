"""Handing somebody a link to their own portal.

Reaches cli/agentbox-portal on the host over host.docker.internal, the same
shape as the speaker and Home Assistant integrations.

## There is no identity parameter, and that is the whole design

The person a link is minted for comes from `CURRENT_IDENTITY` — resolved from
the bearer token before any tool runs — and never from an argument. If the
assistant could name the identity, an instruction embedded in an email could
name one too, and one poisoned context would reach into another person's
account. The gateway's own module docstring argues this at length; this tool
would be the obvious place to quietly break it, because "who is the link for"
looks so much like an ordinary parameter.

So `request_signin_link` mints for whoever is asking, and there is no way to
ask on somebody else's behalf. An admin adding a new person uses the invite
form on the Operations page, which is a deliberate human act with a session
behind it.

## Why this is safe to hand the assistant at all

The link is ORIGIN_AGENT, which the portal treats as read-mostly: it can
review memories and connector status, and it cannot approve a memory,
disconnect an account, connect a chat account, invite anybody, or change a
household setting. The assistant can read a link it delivers — it delivers
over channels it can read, and no amount of care changes that — so the link
is built to be worth little to whoever reads it.

That is `constrain rather than gate` applied to a credential: rather than
approving each mint, the thing minted is bounded.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from mcp_base import ToolError, schema_object

from integrations._client import CURRENT_IDENTITY

PORTAL_URL = os.environ.get("AGENTBOX_PORTAL_INTERNAL_URL",
                            "http://host.docker.internal:8771")
PORTAL_TOKEN = os.environ.get("AGENTBOX_PORTAL_AGENT_TOKEN", "")

TOOLS = [
    {"name": "request_signin_link",
     "description": "Get the person you are talking to a sign-in link for "
                    "their Agentbox portal, where they can review what you "
                    "have proposed remembering and see which accounts you can "
                    "reach. The link is for them — there is no way to request "
                    "one for somebody else. It expires quickly, works once, "
                    "and is deliberately limited: it can read, but it cannot "
                    "approve a memory or change any account, because you can "
                    "see any link you send. Tell them that last part; landing "
                    "on a page where the buttons refuse is otherwise "
                    "confusing.",
     "inputSchema": schema_object({})},
    {"name": "propose_invite",
     "description": "Draft an invitation for somebody who does not live here "
                    "yet, so they can be given their own account. This does "
                    "NOT send anything: it writes a draft that the household "
                    "admin sees on their Operations page, and they decide "
                    "whether it goes out. Say that plainly when you use it — "
                    "promising somebody an invitation is on its way would be "
                    "wrong. Give an email address, a Discord user id, or "
                    "both, so there is somewhere for it to go once approved.",
     "inputSchema": schema_object({
         "account_name": {"type": "string",
                          "description": "The short account name they would "
                                         "get, lowercase, for example sam."},
         "display_name": {"type": "string",
                          "description": "What to call them in the message."},
         "email": {"type": "string",
                   "description": "Where to email the invitation."},
         "discord_user_id": {"type": "string",
                             "description": "Discord user id to DM it to."},
     }, required=["account_name"])},
]


def dispatch(name, args):
    if name == "propose_invite":
        return _propose_invite(args)
    if name != "request_signin_link":
        raise ToolError(f"unknown tool: {name}")

    identity = CURRENT_IDENTITY.get()
    if not identity:
        # Single-operator deployments authenticate with one shared token and
        # no identity. Refusing is right: there is no way to know who the link
        # would be for, and guessing would mint one for whoever is first in a
        # config file.
        raise ToolError(
            "this gateway is not configured with per-person identities, so "
            "there is no way to tell whose link this would be. An operator "
            "can hand one over with `agentbox-portal link <name>`.")
    if not PORTAL_TOKEN:
        raise ToolError(
            "the portal is not accepting assistant-minted links "
            "(AGENTBOX_PORTAL_AGENT_TOKEN is unset on this gateway).")

    request = urllib.request.Request(
        PORTAL_URL + "/agent/link",
        data=urllib.parse.urlencode({"identity": identity}).encode(),
        method="POST",
        headers={"X-Agentbox-Portal-Token": PORTAL_TOKEN,
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            # The portal caps links per person per hour. A refusal is an
            # answer — say so plainly rather than making the assistant retry
            # into a wall.
            raise ToolError(
                "too many sign-in links have been sent to this person in the "
                "last hour. Wait, or ask an operator to hand one over.") from None
        raise ToolError(f"the portal refused to mint a link "
                        f"({exc.code}).") from None
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"portal unreachable: {type(exc).__name__}") from None


# --- proposing an invitation ---------------------------------------------------
#
# This is the one tool here that names somebody other than the caller, and the
# module docstring above explains why that is normally forbidden: an
# instruction embedded in an email could name a person too.
#
# It exists anyway because an invitation is *for* somebody who has no session
# and no identity, so there is nothing to bind it to. What replaces the
# binding is that this tool does not act. It writes a draft; an admin on the
# Operations page decides whether anything is sent. So the poisoned-email case
# ends with a household member reading a request they did not make, which is
# the outcome the approval step exists to produce.
#
# Deciding is withheld from the assistant by the same capability that withholds
# inviting, so a link this assistant minted cannot approve its own draft.


def _propose_invite(args):
    # Named `account_name`, not `identity`, and the distinction is the reason
    # this tool is allowed to exist. `identity` in this codebase means "who
    # the assistant is acting as", which never comes from an argument. This is
    # the opposite: a label for an account that does not exist yet, belonging
    # to somebody with no session. Reusing the word would have made an
    # ordinary parameter look like the forbidden one — and did, until the
    # suite caught it.
    account_name = str(args.get("account_name") or "").strip().lower()
    if not account_name:
        raise ToolError("a short account name is required, for example sam.")
    address = str(args.get("email") or "").strip()
    user_id = str(args.get("discord_user_id") or "").strip()
    if not address and not user_id:
        raise ToolError(
            "give an email address or a Discord user id — an invitation with "
            "nowhere to go cannot be sent even once it is approved.")
    if not PORTAL_TOKEN:
        raise ToolError(
            "the portal is not accepting drafts from the assistant "
            "(AGENTBOX_PORTAL_AGENT_TOKEN is unset on this gateway).")

    payload = {"account_name": account_name, "email": address,
               "discord_user_id": user_id,
               "display_name": str(args.get("display_name") or "").strip()}
    request = urllib.request.Request(
        PORTAL_URL + "/agent/propose-invite",
        data=urllib.parse.urlencode(payload).encode(),
        method="POST",
        headers={"X-Agentbox-Portal-Token": PORTAL_TOKEN,
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            raise ToolError(
                "the portal refused that draft — check the account name is "
                "lowercase letters, digits, dashes or underscores.") from None
        raise ToolError(
            f"the portal refused the draft ({exc.code}).") from None
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"portal unreachable: {type(exc).__name__}") from None
