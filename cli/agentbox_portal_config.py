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

# The largest request body this will read. Every form here is a few short
# fields, so 64 KiB is far above any of them and far below an upload.
MAX_BODY_BYTES = 64 * 1024

# How many drafted invitations may wait unanswered. The assistant can write
# them but not send them, so the safeguard is an admin reading each one. That
# does not work with forty in the queue. A flood gains no privilege, but it can
# make an admin approve without reading, so the limit is on queue depth. It
# clears as the admin acts.
MAX_PENDING_PROPOSALS = int(
    os.environ.get("AGENTBOX_PORTAL_MAX_PROPOSALS", "5"))
# Lets the assistant make a sign-in link so a person need not go and find an
# email. Unset means the endpoint does not exist, so a capability nobody
# configured is not available.
AGENT_TOKEN = os.environ.get("AGENTBOX_PORTAL_AGENT_TOKEN", "").strip()

# identity -> email, as "alex:alex@example.com,sam:sam@example.com".
# Addresses are stored beside the identity list, not in it, so adding an
# address cannot change an authentication token.

# The one mail value with no page: the envelope From, which is almost always
# the account itself and is only separate for relays that require it.
SMTP_FROM = os.environ.get("AGENTBOX_SMTP_FROM", "")

# A client id is not a secret, so the split below works. This page can
# *start* Google's consent flow and receive an authorisation code, but a code
# is useless without the client secret that exchanges it. The secret
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

# Broad Drive read is opt-in, using the same flag as oauth-setup.py, so all
# three ways of granting a credential enforce the same default.
#
# drive.file alone sees only files the assistant created, so a search for a
# document the person made finds nothing. With the flag set, the assistant can
# read anything the person can, and still cannot write outside its folder,
# delete or share, because those do not exist.
if os.environ.get("GOOGLE_ENABLE_DRIVE_READ_ALL", "").strip().lower() in (
        "1", "true", "yes"):
    GOOGLE_SCOPES += " https://www.googleapis.com/auth/drive.readonly"

# Same variable and default as cli/agentbox. Two names for one endpoint could
# leave the portal pointed at nothing without any error.
PUBLIC_URL = os.environ.get("AGENTBOX_PORTAL_URL", "http://127.0.0.1:8771")

# Where Google sends someone back after consent. This is separate from
# PUBLIC_URL because the two have conflicting requirements. PUBLIC_URL must
# resolve from a phone, or sign-in links and invitations are useless. Google
# only accepts HTTPS on a real hostname, or loopback, and loopback on a phone
# is the phone.
#
# With the two kept apart, a box with no extra infrastructure gets onboarding,
# tasks, memory and house control over plain LAN HTTP. Only granting Google
# consent from another device needs a hostname. Pointing both at one https://
# name provides it.
#
# The default is loopback because it always works. Consent then happens in a
# browser on this box, so a household has to use the box directly once.
OAUTH_REDIRECT_BASE = os.environ.get("AGENTBOX_OAUTH_REDIRECT_BASE",
                                     "http://127.0.0.1:8771")

def oauth_redirect_uri() -> str:
    """The redirect URI, built in one place.

    The consent request and the token exchange must send Google the identical
    string, which Google compares to the registered one.
    """
    return f"{OAUTH_REDIRECT_BASE.rstrip('/')}/google/callback"
MEMORY_BRIDGE_URL = os.environ.get("AGENTBOX_MEMORY_BRIDGE",
                                   "http://127.0.0.1:3471")

SETTINGS = portal.agentbox_settings.SettingsStore(directory=STATE)
# Device permissions live on the policy mount, not in the portal's private
# directory, because a container has to read them. See
# agentbox_household.
HOUSEHOLD = portal.agentbox_household.HouseholdPolicy(directory=Path(os.environ.get(
    "AGENTBOX_POLICY_DIR",
    os.path.expanduser("~/.local/state/agentbox/policy"))))

ADMIN = "admin"
MEMBER = "member"

# How a session was authenticated, which decides what it may do.
#
# EMAIL    the person proved control of their inbox and their browser.
# OPERATOR the operator made a link and handed it over directly.
# AGENT    the assistant made it on request, to save checking email.
#
# AGENT links cannot be trusted, because the assistant can read whatever
# channel it delivers them on. Anything an AGENT session can do, the assistant
# can do. So those sessions grant nothing the assistant could not already do,
# and stealing one gains nothing.
ORIGIN_EMAIL = "email"
ORIGIN_OPERATOR = "operator"
ORIGIN_AGENT = "agent"
# A link opened in a browser other than the one that asked for it.
#
# On a phone this is the normal case. Discord and most mail apps open links in
# their own browser, which has its own cookies, so the nonce from the request
# is not there.
#
# So the binding sets privilege, and does not block access. The same browser
# proves the person opening the link is the one who asked, and gets
# everything. Any other browser can read but not approve a memory or
# disconnect an account. These are the same limits as an assistant-made link,
# because someone who only read the link could be the one opening it.
ORIGIN_CHAT = "chat"

# Capabilities withheld from an assistant-made session.
#
# Approving a memory is the important one. The review gate is the assistant's
# only route to lasting memory, and approving its own proposals would remove
# the person from the loop. Disconnecting is here because revoking someone's
# account access must be the person's own decision.
#
# Reading your own memories, seeing account status and starting a reconnect
# that the operator side finishes are all safe.
AGENT_WITHHELD = frozenset({
    "memory:decide_own",
    "memory:decide_household",
    "connector:disconnect_own",
    "connector:pair_chat",
    # `admins` decides who may approve a household memory and who sees this
    # page at all, and `identity_emails` decides where a sign-in link is
    # delivered. A link the assistant minted must not be able to change
    # either, because that would be a privilege escalation.
    "ops:write_settings",
    # A session the assistant created also cannot widen what the assistant
    # may actuate in the house.
    "ops:write_household",
    # Creating an identity is granting access. If the assistant could invite,
    # the assistant could decide who lives here.
    "ops:invite",
})

# Withheld from assistant-made links only, not from links delivered by chat.
#
# For writes the two are the same risk, since neither proves the person acting
# is the one who asked. For reading they differ.
#
#   ORIGIN_AGENT  the assistant made the link and can read it, so anything the
#                 link can see, the assistant can see.
#   ORIGIN_CHAT   a person has the link on their phone. It is downgraded
#                 because it was opened in a different browser, not because
#                 anyone untrusted holds it.
#
# Operations lists who lives here, with their addresses and Discord ids.
# Withholding it from chat links would stop an admin checking the box from
# their phone. Withholding it from the assistant alone has no such cost.
AGENT_ONLY_WITHHELD = frozenset({
    "ops:read_health",
})

# What each role may do, listed explicitly so a capability nobody granted is
# absent. Deny by default, so an action missing from this table is refused.
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
        # Only capabilities that something checks belong here. One that is
        # granted and never checked is as misleading as one that is checked
        # and never granted.
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
