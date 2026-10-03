"""Giving someone a link to their own portal.

Reaches cli/agentbox-portal on the host through host.docker.internal, like the
speaker and Home Assistant integrations.

## There is no identity parameter

The link is for whoever `CURRENT_IDENTITY` says, which comes from the bearer
token before any tool runs, never from an argument. If the assistant could name
the person, an instruction in an email could too. This is stated here because
"who is the link for" would otherwise look like an ordinary parameter.

So `request_signin_link` makes a link for whoever is asking, and nobody can ask
on someone else's behalf. Adding a new person goes through the invite form on
the Operations page.

## Why it is safe to give the assistant

The link is ORIGIN_AGENT, which can review memories and account status and
cannot approve a memory, disconnect an account, connect a chat account, invite
anyone or change a household setting. The assistant can read any link it
delivers, so the link has little power for whoever reads it. Links are not
approved one by one. Instead, what any link can do is limited.
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
                    "reach. The link is for them. There is no way to request "
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
                    "whether it goes out. Say that plainly when you use it, because "
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
        # no identity. These are refused. There is no way to know who the
        # link would be for, and guessing would mint one for whoever is first
        # in a config file.
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
            # The portal caps links per person per hour. Report the cap
            # clearly, so the assistant does not keep retrying.
            raise ToolError(
                "too many sign-in links have been sent to this person in the "
                "last hour. Wait, or ask an operator to hand one over.") from None
        raise ToolError(f"the portal refused to mint a link "
                        f"({exc.code}).") from None
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"portal unreachable: {type(exc).__name__}") from None


# --- proposing an invitation ---------------------------------------------------
#
# This is the one tool here that names somebody other than the caller. The
# module docstring above explains why that is normally forbidden. An
# instruction embedded in an email could name a person too.
#
# It exists because an invitation is for somebody who has no session and no
# identity, so there is nothing to bind it to. Instead, this tool does not
# act. It writes a draft, and an admin on the Operations page decides whether
# anything is sent. If an email injects an invitation, a household member sees
# a request they did not make and can reject it at the approval step.
#
# Deciding is withheld from the assistant by the same capability that withholds
# inviting, so a link this assistant minted cannot approve its own draft.


def _propose_invite(args):
    # Named `account_name`, not `identity`. Here `identity` means who the
    # assistant is acting for, which never comes from an argument. This is the
    # opposite, a label for an account that does not exist yet, so it must not
    # look like the forbidden parameter.
    account_name = str(args.get("account_name") or "").strip().lower()
    if not account_name:
        raise ToolError("a short account name is required, for example sam.")
    address = str(args.get("email") or "").strip()
    user_id = str(args.get("discord_user_id") or "").strip()
    if not address and not user_id:
        raise ToolError(
            "give an email address or a Discord user id. An invitation with "
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
                "the portal refused that draft. Check the account name is "
                "lowercase letters, digits, dashes or underscores.") from None
        if exc.code == 429:
            # Report the refusal clearly so the model does not retry. The
            # message to relay is that somebody has to look at the queue. The
            # tool has not failed.
            raise ToolError(
                "there are already several invitations waiting for the "
                "household to decide on, so this one was not added. Ask them "
                "to look at the waiting list on their Operations page "
                "first.") from None
        raise ToolError(
            f"the portal refused the draft ({exc.code}).") from None
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"portal unreachable: {type(exc).__name__}") from None
