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

    Sources are additive because each knows a different subset: sign-in
    addresses, paired chat accounts, whoever is already an admin, and any
    names published for the assistant. The last is names only and never
    tokens — the portal is the LAN-facing surface and has no business holding
    a bearer credential.
    """
    # `also` is the form's own submission, merged over what is stored. The
    # settings page saves identity_emails and admins under one button, so
    # checking admins against stored state alone refused the obvious flow:
    # add somebody's address and name them admin in the same save.
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

    Empty means nobody is an admin, which is the correct failure direction: a
    misread setting should remove privilege, never hand it out.
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

    Two gates, and both must pass: the role says what this person may ever do,
    the origin says what this particular sign-in may do. An unknown origin is
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

    `can` collapses two independent gates into one boolean, which is right for
    deciding and wrong for explaining. A member refused an admin-only action
    was being told their *link* was the problem and sent to request another
    one — which produces an identical refusal, because the link was never the
    issue. Saying "sign in differently" to somebody whose role is the
    constraint is worse than saying nothing.
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

    A plain SHA-256 rather than a password KDF on purpose: these secrets are
    32 bytes from secrets.token_urlsafe, not human-chosen, so there is no
    dictionary to stretch against and no benefit worth the latency.
    """
    return hashlib.sha256(secret.encode()).hexdigest()

LINK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

def link_path(link_id: str) -> Path:
    """Where a link record lives. Rejects anything that is not an id.

    `link_id` arrives from the query string of an unauthenticated request, and
    went into a path unchecked. Redemption still required a matching
    secret_hash, so traversal alone proved nothing — but "unexploitable today"
    is a property of the code around it, not of this function, and it is the
    kind of thing that stops being true when somebody adds a write. mint_link
    generates token_urlsafe ids, which this matches exactly.
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
    """Create a single-use login link. Returns (url, link_id).

    `request_nonce` binds the link to the browser that asked for it: the same
    value is set as a cookie, and redemption requires both. Someone who reads
    the link — including an assistant with access to the inbox it was sent to —
    holds only half of what is needed.

    An empty nonce is accepted for operator-issued links (`portal link`), where
    the operator hands the URL over directly and there is no browser to bind to.

    Raises RuntimeError past the hourly cap rather than issuing quietly, so a
    flood is visible to whoever triggered it.
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
        # The hash, never the secret. This file is not a login.
        "secret_hash": _hash(secret),
        # Hashed for the same reason the secret is: this file is not a login.
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

    Three fields rather than two because origin and reason are different
    things, and a slot meaning "origin on success, reason on failure" is how a
    caller ends up treating an error string as a privilege level.

    Marks the link used *before* returning, so two concurrent redemptions of a
    stolen link cannot both succeed.
    """
    generic = "This link is not valid. Ask for a new one."
    try:
        path = link_path(link_id)
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # ValueError covers both a malformed id and unreadable JSON, and both
        # get the same sentence as a link that never existed. An id shaped
        # wrongly must not be distinguishable from one that is merely wrong —
        # and it must certainly not take the handler thread down, since a
        # closed connection is itself an answer.
        return "", "", generic
    if not hmac.compare_digest(str(record.get("secret_hash", "")),
                               _hash(secret)):
        return "", "", generic
    # The browser binding. A link requested through the sign-in form carries a
    # nonce hash, and only the browser holding the matching cookie may spend it.
    expected = str(record.get("nonce_hash", ""))
    origin = str(record.get("origin", portal.ORIGIN_AGENT))
    if expected and not hmac.compare_digest(expected, _hash(request_nonce)):
        # Not a refusal — a downgrade. See ORIGIN_CHAT.
        origin = portal.ORIGIN_CHAT
    if record.get("used_at"):
        return "", "", "This link has already been used. Ask for a new one."
    if portal.now() > int(record.get("expires_at", 0)):
        return "", "", "This link has expired. Ask for a new one."
    if origin == portal.ORIGIN_CHAT:
        # Deliberately not spent. A downgraded redemption must not let whoever
        # read the message lock the real person out — they would request
        # another, it would arrive on the same channel, and the same reader
        # would burn that one too. Full access is still single-use; limited
        # access simply does not consume the link.
        return str(record.get("identity", "")), origin, ""

    record["used_at"] = portal.now()
    try:
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except OSError:
        # Single-use that cannot be enforced is not single-use. Refuse rather
        # than hand out a link that stays live — the same reasoning as an
        # unwritable grant-consumption record in policy_gate.
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

    The role is recomputed from the current admin list rather than trusted from
    the stored record: removing someone from AGENTBOX_ADMINS must take effect
    immediately, not whenever their session happens to expire.
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
    # Origin is trusted from the record because it was set at redemption from
    # the link, not from anything the browser said. An absent value means a
    # session predating this field, treated as agent-minted — the safe way to
    # read a value that is not there.
    return {"identity": identity, "role": role_of(identity),
            "origin": str(record.get("origin", portal.ORIGIN_AGENT))}

def end_session(sid: str) -> None:
    try:
        session_path(_hash(sid)).unlink()
    except OSError:
        pass

# --- server --------------------------------------------------------------------

def state_secret() -> str:
    """The key the consent state is derived from. Random, persisted, 0600.

    This used to fall back to a hash of the STATE directory *path* — a value
    anyone who can guess `~/.local/state/agentbox/portal` can compute. The
    consent state is the only thing standing between a crafted callback link
    and someone else's authorisation code landing in your reconnect record
    (SameSite=Lax still sends the session cookie on a top-level GET, and the
    callback is a GET), so it must be underivable, not merely per-identity.

    Persisted to disk rather than derived, which keeps the property the
    derivation was for: a restart between consent and callback does not strand
    the user, because the file is still there. O_EXCL so two concurrent first
    requests cannot mint two different secrets.
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
    compute a valid state at all.
    """
    return hmac.new(state_secret().encode(), f"google:{identity}".encode(),
                    hashlib.sha256).hexdigest()

def cookie_value(header: str, name: str) -> str:
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return ""
