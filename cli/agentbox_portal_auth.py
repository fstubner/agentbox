"""Who someone is and what they may do: identities, roles, sign-in links and sessions.

Loaded by cli/agentbox-portal, never imported on its own. Other portal
names are reached through `portal`, the portal module itself.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# The portal that loaded this file, which registers it as "<portal>.<part>".
portal = sys.modules[__name__.rpartition(".")[0]]

__all__ = [
    "known_identities",
    "admin_names",
    "email_map",
    "identity_for_email",
    "role_of",
    "can",
    "refusal",
    "require",
    "_hash",
    "LINK_ID",
    "link_path",
    "_recent_link_count",
    "mint_link",
    "redeem_link",
    "session_path",
    "new_session",
    "load_session",
    "end_session",
    "cookie_value",
    "state_secret",
    "consent_state",
]


# --- identities and roles ------------------------------------------------------

def known_identities(also: dict[str, str] | None = None) -> set[str]:
    """Every name this box recognises as a person.

    The sources add up, because each knows a different subset: sign-in
    addresses, paired chat accounts, current admins and any names published
    for the assistant. That last source is names only, never tokens, because
    the portal faces the LAN and should not hold a bearer credential.
    """
    # `also` is the form's own submission, merged over what is stored. The
    # settings page saves identity_emails and admins under one button.
    # Checking admins against stored state alone would refuse adding
    # somebody's address and naming them admin in the same save.
    names = set(email_map().values()) | portal.discord_identities()
    for pair in (also or {}).get("identity_emails", "").split(","):
        name, _, address = pair.partition(":")
        if name.strip() and address.strip():
            names.add(name.strip())
    names |= {n.strip() for n in portal.SETTINGS.value("admins").split(",") if n.strip()}
    names |= {n.strip() for n in os.environ.get(
        "AGENTBOX_IDENTITY_NAMES", "").replace(" ", ",").split(",") if n.strip()}
    return names

def admin_names() -> set[str]:
    """Identities with the admin role.

    Empty means nobody is an admin. A misread setting should remove
    privilege, never grant it.
    """
    return {n.strip() for n in portal.SETTINGS.value("admins").split(",") if n.strip()}

def email_map() -> dict[str, str]:
    """Lowercased email -> identity."""
    mapping: dict[str, str] = {}
    for pair in portal.SETTINGS.value("identity_emails").split(","):
        name, _, address = pair.partition(":")
        if name.strip() and address.strip():
            mapping[address.strip().lower()] = name.strip()
    return mapping

def identity_for_email(address: str) -> str:
    return email_map().get(address.strip().lower(), "")

def role_of(identity: str) -> str:
    return portal.ADMIN if identity in admin_names() else portal.MEMBER

def can(role: str, capability: str, origin: str = portal.ORIGIN_EMAIL) -> bool:
    """Whether this session may exercise `capability`.

    Two gates must both pass. The role says what this person may ever do. The
    origin says what this particular sign-in may do. An unknown origin is
    treated as agent-minted, which is the safe direction for a value that
    arrived from disk.
    """
    if capability not in portal.ROLE_CAPABILITIES.get(role, frozenset()):
        return False
    if origin in (portal.ORIGIN_EMAIL, portal.ORIGIN_OPERATOR):
        return True
    if capability in portal.AGENT_WITHHELD:
        return False
    # Anything not explicitly a chat origin is treated as agent-minted, which
    # is the safe direction for a value that arrived from disk.
    return origin == portal.ORIGIN_CHAT or capability not in portal.AGENT_ONLY_WITHHELD

def refusal(role: str, capability: str, origin: str = portal.ORIGIN_EMAIL) -> str:
    """Which gate refused, as a flash key. Empty when nothing refused.

    `can` combines two gates into one yes or no. That is enough to decide,
    but not to explain a refusal. A member refused an admin-only action
    should be told about their role, not sent to request another link that
    would be refused the same way.
    """
    if capability not in portal.ROLE_CAPABILITIES.get(role, frozenset()):
        return "admin_only"
    if origin in (portal.ORIGIN_EMAIL, portal.ORIGIN_OPERATOR):
        return ""
    if capability in portal.AGENT_WITHHELD:
        return "agent_link_cannot_configure"
    if origin != portal.ORIGIN_CHAT and capability in portal.AGENT_ONLY_WITHHELD:
        return "agent_link_cannot_read_ops"
    return ""

def require(role: str, capability: str, origin: str = portal.ORIGIN_EMAIL) -> None:
    if not can(role, capability, origin):
        raise PermissionError(capability)

# --- magic links ---------------------------------------------------------------

def _hash(secret: str) -> str:
    """Links are stored hashed, so reading this directory yields nothing usable.

    A plain SHA-256 rather than a password KDF: these secrets are
    32 bytes from secrets.token_urlsafe, not human-chosen, so there is no
    dictionary to stretch against and no benefit worth the latency.
    """
    return hashlib.sha256(secret.encode()).hexdigest()

LINK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

def link_path(link_id: str) -> Path:
    """Where a link record lives. Refuses anything that is not an id.

    The id comes from the query string of an unauthenticated request. Using
    the secret hash also stops a traversal from proving anything, but that
    depends on the surrounding code, so the id is checked here too. It matches
    exactly what mint_link generates.
    """
    if not LINK_ID.match(link_id or ""):
        raise ValueError("not a link id")
    return portal.STATE / "links" / f"{link_id}.json"

def _recent_link_count(identity: str) -> int:
    directory = portal.STATE / "links"
    if not directory.is_dir():
        return 0
    cutoff = portal.now() - 3600
    count = 0
    for entry in directory.glob("*.json"):
        try:
            record = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if record.get("identity") == identity and \
                int(record.get("created_at", 0)) > cutoff:
            count += 1
    return count

def mint_link(identity: str, base_url: str, request_nonce: str = "",
              origin: str = portal.ORIGIN_OPERATOR) -> tuple[str, str]:
    """Create a single-use sign-in link. Returns (url, link_id).

    `request_nonce` binds the link to the browser that asked for it. The same
    value is set as a cookie, and using the link needs both. Someone who only
    reads the link, including an assistant that can read the inbox, holds half
    of what is needed.

    An empty nonce is accepted for links the operator hands over directly
    (`portal link`), where there is no browser to bind to.

    Past the hourly cap this raises RuntimeError instead of issuing a link,
    so whoever caused the flood sees it.
    """
    if _recent_link_count(identity) >= portal.MAX_LINKS_PER_HOUR:
        raise RuntimeError(
            f"{identity} has already been sent {portal.MAX_LINKS_PER_HOUR} links in "
            f"the last hour; refusing to mint another")
    link_id = secrets.token_urlsafe(9)
    secret = secrets.token_urlsafe(32)
    record = {
        "id": link_id,
        "identity": identity,
        # The hash, never the secret, so this file cannot be used to sign in.
        "secret_hash": _hash(secret),
        # Hashed for the same reason as the secret.
        "nonce_hash": _hash(request_nonce) if request_nonce else "",
        "origin": origin,
        "created_at": portal.now(),
        "expires_at": portal.now() + portal.LINK_TTL_SECONDS,
        "used_at": None,
    }
    path = link_path(link_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    path.chmod(0o600)
    query = urllib.parse.urlencode({"id": link_id, "k": secret})
    return f"{base_url.rstrip('/')}/login?{query}", link_id

def redeem_link(link_id: str, secret: str,
                request_nonce: str = "") -> tuple[str, str, str]:
    """Consume a link, returning (identity, origin, reason).

    It returns three fields because origin and reason are different things.
    One slot meaning "origin on success, reason on failure" could lead a
    caller to treat an error string as a privilege level.

    Marks the link used *before* returning, so two concurrent redemptions of a
    stolen link cannot both succeed.
    """
    generic = "This link is not valid. Ask for a new one."
    try:
        path = link_path(link_id)
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A malformed id and unreadable JSON both get the same answer as a
        # link that never existed, so a badly shaped id tells an attacker
        # nothing, and neither can crash the handler.
        return "", "", generic
    if not hmac.compare_digest(str(record.get("secret_hash", "")),
                               _hash(secret)):
        return "", "", generic
    # The browser binding. A link requested through the sign-in form carries a
    # nonce hash, and only the browser holding the matching cookie may spend it.
    expected = str(record.get("nonce_hash", ""))
    origin = str(record.get("origin", portal.ORIGIN_AGENT))
    if expected and not hmac.compare_digest(expected, _hash(request_nonce)):
        # The link is downgraded, not refused. See ORIGIN_CHAT.
        origin = portal.ORIGIN_CHAT
    if record.get("used_at"):
        return "", "", "This link has already been used. Ask for a new one."
    if portal.now() > int(record.get("expires_at", 0)):
        return "", "", "This link has expired. Ask for a new one."
    if origin == portal.ORIGIN_CHAT:
        # Not used up. Otherwise whoever read the message could lock the real
        # person out, and would also read the next link sent on the same
        # channel. Full access is still single-use, and limited access does
        # not consume the link.
        return str(record.get("identity", "")), origin, ""

    record["used_at"] = portal.now()
    try:
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except OSError:
        # If single use cannot be enforced, refuse rather than hand out a link
        # that stays live, as policy_gate does with grants.
        return "", "", generic
    return str(record.get("identity", "")), origin, ""

# --- sessions ------------------------------------------------------------------

def session_path(sid: str) -> Path:
    return portal.STATE / "sessions" / f"{sid}.json"

def new_session(identity: str, origin: str = portal.ORIGIN_EMAIL) -> str:
    sid = secrets.token_urlsafe(24)
    record = {"identity": identity, "role": role_of(identity),
              "origin": origin, "expires_at": portal.now() + portal.SESSION_TTL_SECONDS}
    path = session_path(_hash(sid))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    path.chmod(0o600)
    return sid

def load_session(sid: str) -> dict | None:
    """Resolve a cookie to (identity, role), or None.

    The role is recomputed from the current admin list, not read from the
    stored record. Removing someone from AGENTBOX_ADMINS must take effect
    immediately, not when their session expires.
    """
    if not sid:
        return None
    try:
        record = json.loads(session_path(_hash(sid)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if portal.now() > int(record.get("expires_at", 0)):
        return None
    identity = str(record.get("identity", ""))
    if not identity:
        return None
    # The origin is trusted from the record because it was set from the link at
    # sign-in, not from anything the browser sent. A missing value is treated
    # as assistant-made, the safe reading.
    return {"identity": identity, "role": role_of(identity),
            "origin": str(record.get("origin", portal.ORIGIN_AGENT))}

def end_session(sid: str) -> None:
    try:
        session_path(_hash(sid)).unlink()
    except OSError:
        pass

# --- server --------------------------------------------------------------------

def state_secret() -> str:
    """The key the consent state is derived from. Random, saved, mode 600.

    The consent state is what stops a crafted callback link putting someone
    else's authorisation code into your reconnect record, because the
    callback is a GET and the session cookie is still sent on one. So it must
    not be derivable from anything guessable, such as the state directory's
    path.

    It is saved to disk, so a restart between consent and callback does not
    strand the person. O_EXCL means two first requests at once cannot create
    two different secrets.
    """
    secret = os.environ.get("AGENTBOX_PORTAL_STATE_SECRET", "")
    if secret:
        return secret
    path = portal.STATE / "state-secret"
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    minted = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text(encoding="utf-8").strip()
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(minted)
    return minted

def consent_state(identity: str) -> str:
    """A state value bound to this identity and this box's own secret.

    Keyed per identity so one person's consent cannot be redirected into
    another's record, and keyed on a random secret so nobody off the box can
    compute a valid state.
    """
    return hmac.new(state_secret().encode(), f"google:{identity}".encode(),
                    hashlib.sha256).hexdigest()

def cookie_value(header: str, name: str) -> str:
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return ""
