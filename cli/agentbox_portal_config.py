"""Portal settings read from the environment, role and origin constants, and flash messages.

Loaded by cli/agentbox-portal, never imported on its own. Other portal
names are reached through `portal`, the portal module itself.
"""
from __future__ import annotations

import os
import sys
import time
import urllib.parse
from pathlib import Path

# The portal that loaded this file, which registers it as "<portal>.<part>".
portal = sys.modules[__name__.rpartition(".")[0]]

__all__ = [
    "service_url",
    "STATE",
    "LINK_TTL_SECONDS",
    "SESSION_TTL_SECONDS",
    "MAX_LINKS_PER_HOUR",
    "MAX_BODY_BYTES",
    "MAX_PENDING_PROPOSALS",
    "AGENT_TOKEN",
    "SMTP_FROM",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_SCOPES",
    "PUBLIC_URL",
    "OAUTH_REDIRECT_BASE",
    "oauth_redirect_uri",
    "MEMORY_BRIDGE_URL",
    "SETTINGS",
    "HOUSEHOLD",
    "ADMIN",
    "MEMBER",
    "ORIGIN_EMAIL",
    "ORIGIN_OPERATOR",
    "ORIGIN_AGENT",
    "ORIGIN_CHAT",
    "AGENT_WITHHELD",
    "AGENT_ONLY_WITHHELD",
    "ROLE_CAPABILITIES",
    "now",
]


STATE = Path(os.environ.get(
    "AGENTBOX_PORTAL_DIR",
    os.path.expanduser("~/.local/state/agentbox/portal")))
LINK_TTL_SECONDS = int(os.environ.get("AGENTBOX_PORTAL_LINK_TTL", "600"))
# Sessions outlive the link but not by much. This page can revoke credentials
# and delete memories, so a forgotten open tab is a real exposure.
SESSION_TTL_SECONDS = int(os.environ.get("AGENTBOX_PORTAL_SESSION_TTL", "1800"))
# A link is a credential. Minting them without limit turns a leaked identity
# name into an unbounded supply of guesses at the delivery channel.
MAX_LINKS_PER_HOUR = int(os.environ.get("AGENTBOX_PORTAL_MAX_LINKS", "5"))

# The largest request body this will read. Every form here is a handful of
# short fields; 64 KiB is far above any of them and far below anything worth
# calling an upload. Until this existed the portal read whatever length a
# request declared, while cli/agentbox-invite — the deliberately *less*
# privileged of the two surfaces — had capped its body since it was written.
MAX_BODY_BYTES = 64 * 1024

# How many drafted invitations may sit unanswered. The assistant may write
# these and cannot send them, so the protection is the admin reading each one
# — which is exactly what stops working when there are forty of them. The
# harm from a flood is not privilege, it is an approval step that degrades
# into clicking, so the bound is on queue depth rather than on rate. It clears
# itself as the admin acts.
MAX_PENDING_PROPOSALS = int(
    os.environ.get("AGENTBOX_PORTAL_MAX_PROPOSALS", "5"))
# Lets the assistant mint a sign-in link so a person does not have to go and
# find an email. Unset means the endpoint does not exist — a capability nobody
# configured should be absent, not merely unused.
AGENT_TOKEN = os.environ.get("AGENTBOX_PORTAL_AGENT_TOKEN", "").strip()

# identity -> email, as "alex:alex@example.com,sam:sam@example.com".
# Addresses live beside the identity list rather than in it so that adding an
# address never risks disturbing an authentication token.

# The one mail value with no page: the envelope From, which is almost always
# the account itself and is only separate for relays that require it.
SMTP_FROM = os.environ.get("AGENTBOX_SMTP_FROM", "")

# A client id is not a secret, which is what makes the split below work: this
# page can *start* Google's consent flow and receive an authorisation code, but
# a code is useless without the client secret that exchanges it. The secret
# stays on the operator side. Same reasoning as cli/agentbox-invite.
# GOOGLE_CLIENT_ID is what the bridge env file already calls it, so the unit
# can source that file directly instead of the operator copying the value into
# a second place where the two can disagree.
GOOGLE_CLIENT_ID = (os.environ.get("AGENTBOX_GOOGLE_CLIENT_ID", "")
                    or os.environ.get("GOOGLE_CLIENT_ID", "")).strip()
GOOGLE_SCOPES = os.environ.get(
    "AGENTBOX_GOOGLE_SCOPES",
    "https://www.googleapis.com/auth/gmail.modify "
    "https://www.googleapis.com/auth/calendar "
    "https://www.googleapis.com/auth/drive.file "
    "https://www.googleapis.com/auth/drive.activity.readonly "
    "https://www.googleapis.com/auth/contacts.readonly")

# Broad Drive read is opt-in, by the same flag and for the same reason as
# services/compose/google-workspace-bridge/oauth-setup.py — which had the
# mechanism while these two consent paths did not, so the safer default was
# only actually enforced on one of the three ways to grant a credential.
#
# drive.file alone sees ONLY files the assistant created. That is the right
# default and it is also why "find that lease in my Drive" could not be
# answered: a full Drive returned nothing and read as empty. With the flag
# set, the assistant can read anything the person can read — and still cannot
# write outside its own folder, delete, or share, because those are absent
# rather than gated.
if os.environ.get("GOOGLE_ENABLE_DRIVE_READ_ALL", "").strip().lower() in (
        "1", "true", "yes"):
    GOOGLE_SCOPES += " https://www.googleapis.com/auth/drive.readonly"

# Same variable and default as cli/agentbox, deliberately. Two names for one
# endpoint is how a portal ends up quietly pointed at nothing.
PUBLIC_URL = os.environ.get("AGENTBOX_PORTAL_URL", "http://127.0.0.1:8771")

# Where Google is told to send somebody back, which is deliberately NOT
# PUBLIC_URL even though it was for a long time.
#
# One value was doing two jobs with incompatible requirements. PUBLIC_URL has
# to resolve from a phone, or every sign-in link and invitation is unusable.
# Google refuses a private IP as a redirect URI and accepts only HTTPS on a
# real hostname, or loopback — and loopback on somebody's phone is their
# phone. Set PUBLIC_URL to something a phone can reach and Google rejects the
# consent flow; set it to loopback and the links go nowhere.
#
# That collision, not Google itself, was the reason this looked like it needed
# Tailscale or a domain to adopt at all. Split apart, a box with no
# infrastructure gets working onboarding, tasks, memory and house control over
# plain LAN HTTP, and the one thing that still wants a hostname — granting
# Google consent from somewhere other than this machine — is an upgrade rather
# than a prerequisite. Point both at the same https:// name to get it.
#
# The default is loopback because that is the setting that always works: it
# means consent is granted in a browser on this box, which for a household is
# a person sitting down at it once.
OAUTH_REDIRECT_BASE = os.environ.get("AGENTBOX_OAUTH_REDIRECT_BASE",
                                     "http://127.0.0.1:8771")

def oauth_redirect_uri() -> str:
    """The redirect URI, derived once.

    Both halves of the flow must send Google the identical string — the
    consent request and the token exchange — and Google compares it to what is
    registered. Two call sites building it separately is how they drift.
    """
    return f"{OAUTH_REDIRECT_BASE.rstrip('/')}/google/callback"
MEMORY_BRIDGE_URL = os.environ.get("AGENTBOX_MEMORY_BRIDGE",
                                   "http://127.0.0.1:3471")

SETTINGS = portal.agentbox_settings.SettingsStore(directory=STATE)
# Device permissions go on the policy mount, not in the portal's private
# directory — a container has to read them. See agentbox_household.
HOUSEHOLD = portal.agentbox_household.HouseholdPolicy(directory=Path(os.environ.get(
    "AGENTBOX_POLICY_DIR",
    os.path.expanduser("~/.local/state/agentbox/policy"))))

ADMIN = "admin"
MEMBER = "member"

# How a session was authenticated. This is not decoration: it decides what the
# session may do.
#
# EMAIL    the person proved control of their inbox and their browser.
# OPERATOR the operator minted a link and handed it over directly.
# AGENT    the assistant minted it on request, so the person did not have to
#          go and check their email.
#
# The last one is convenient and cannot be trusted, for a reason that has
# nothing to do with the assistant misbehaving: whatever channel it delivers
# the link over, it can read that channel. It is the Discord bot. So any
# capability an AGENT session has is a capability the assistant effectively
# has.
#
# The response is not to forbid agent-minted links — they are genuinely useful,
# and the assistant already holds most of what the portal exposes. It is to
# make an agent-minted session grant *nothing the assistant could not already
# do*, so stealing one buys nothing.
ORIGIN_EMAIL = "email"
ORIGIN_OPERATOR = "operator"
ORIGIN_AGENT = "agent"
# Opened in a browser other than the one that asked for the link.
#
# This is the ordinary case on a phone, not an attack: Discord and most mail
# apps open links in their own in-app browser, which has its own cookie jar,
# so the nonce set when the link was requested is simply not there. Treating
# that as a dead end made the whole delivery useless to anyone not sitting at
# the same desktop browser they started from.
#
# So the binding decides the *privilege*, not the access. Same browser proves
# the person who asked is the person opening it, and gets everything. Any
# other browser gets a session that can read but not approve a memory or
# disconnect an account — the same limits as an assistant-minted link, for the
# same reason: whoever merely read the link could be doing this.
ORIGIN_CHAT = "chat"

# Capabilities withheld from an agent-minted session.
#
# Approving a memory is the one that matters. The review gate is the
# assistant's only route to durable memory, and self-approval collapses it
# entirely — it would be writing its own long-term memory with no human in the
# loop. Disconnecting is here because revoking someone's account access on
# their behalf is not a convenience.
#
# Everything else — reading your own memories, seeing connector status,
# starting a reconnect that an operator must still finish — is safe to reach
# from a link the assistant produced.
AGENT_WITHHELD = frozenset({
    "memory:decide_own",
    "memory:decide_household",
    "connector:disconnect_own",
    "connector:pair_chat",
    # `admins` decides who may approve a household memory and who sees this
    # page at all, and `identity_emails` decides where a sign-in link is
    # delivered. A link the assistant minted must not be able to move either:
    # that is the difference between a convenience and a privilege escalation.
    "ops:write_settings",
    # Widening what the assistant may actuate in the house is likewise not
    # something a session the assistant created gets to do.
    "ops:write_household",
    # Creating an identity is granting access. If the assistant could invite,
    # the assistant could decide who lives here.
    "ops:invite",
})

# Withheld from assistant-minted links only, not from chat-delivered ones.
#
# AGENT_WITHHELD above covers both because for *writes* they are the same
# risk: neither proves the person doing it is the person who asked. Reading is
# where they part company, and conflating them was costing something real.
#
#   ORIGIN_AGENT  the assistant made this link and can read it, so anything
#                 the link can see, the assistant can see.
#   ORIGIN_CHAT   a person has this link on their phone. It is downgraded
#                 because it was not opened in the browser that asked for it,
#                 not because anyone untrusted holds it.
#
# Operations lists who lives here, their sign-in addresses and their Discord
# ids. Withholding it from both would have stopped an admin checking on the
# box from their phone — an ordinary thing to want — in order to keep it from
# the assistant. Withholding it from the assistant alone costs nothing.
AGENT_ONLY_WITHHELD = frozenset({
    "ops:read_health",
})

# What each role may do. Enumerated rather than computed: a capability that
# nobody granted should be absent, not merely unreachable by the current UI.
# Deny by default — an action missing from this table is refused.
ROLE_CAPABILITIES = {
    MEMBER: frozenset({
        "memory:read_own",
        "memory:decide_own",
        "connector:read_own",
        "connector:disconnect_own",
        "connector:pair_chat",
    }),
    ADMIN: frozenset({
        "memory:read_own",
        "memory:decide_own",
        "memory:decide_household",
        "connector:read_own",
        "connector:disconnect_own",
        "connector:pair_chat",
        "ops:read_health",
        # `ops:stage_messaging_credential` was removed on 2026-08-18: it was
        # granted here and checked nowhere. This table is deliberately
        # enumerated so that a capability nobody granted is absent rather than
        # merely unreachable; one that is granted and never consulted is the
        # same confusion pointing the other way. It comes back with its check,
        # or not at all.
        "ops:write_settings",
        "ops:write_household",
        "ops:invite",
    }),
}

def now() -> int:
    return int(time.time())


def service_url(port: int) -> str:
    """A link to another service on this box, on the host people already use
    to reach the portal."""
    host = urllib.parse.urlparse(PUBLIC_URL).hostname or "127.0.0.1"
    return f"http://{host}:{port}"
