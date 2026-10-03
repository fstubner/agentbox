"""What may be controlled: security domains that never are, allowed domains,
and the household's entity allowlist."""
from __future__ import annotations

import json
import os
from pathlib import Path

from bridge_base import BridgeError

# Domains this service never acts on, whatever the allowlist says. A hard
# refusal in the process holding the token, so a mistaken allowlist cannot open
# a door. `camera` is here because this bridge never moves, records or arms a
# camera. Reading a single frame is a separate allowlisted route.
SECURITY_DOMAINS = frozenset({"lock", "alarm_control_panel", "cover",
                              "garage_door", "vacuum", "camera"})

# Domains controllable without naming every entity. A per-entity list for
# every light means either nothing works or someone pastes in everything,
# including entities that should not be there.
#
# Only light and scene, chosen by consequence.
#
#   light   reversible and visible, and the worst case is annoying
#   scene   an arrangement a person chose in advance
#
# Left out on purpose.
#
#   switch        a switch is whatever it is wired to, such as a heater or a
#                 pump, so the domain says nothing about risk
#   climate       costs money and affects people asleep, and the policy
#                 already makes it approval_required
#   media_player  casting shows content on a screen other people can see
#
# Anything else has to be named in the allowlist below.
CONTROLLABLE_DOMAINS = frozenset(
    d.strip() for d in os.environ.get("HA_CONTROLLABLE_DOMAINS",
                                      "light,scene").split(",")
    if d.strip())

# Individual entities allowed on top of the domains above, such as a
# media_player to cast to or a specific switch.
#
# Read from the read-only policy mount, which the portal writes and the
# assistant cannot, as with grants. That makes adding a device something the
# household does on the Operations page. The environment variable is still used
# when the file is absent.
POLICY_FILE = Path(os.environ.get("HA_POLICY_FILE", "/policy/household.json"))

_ENV_CONTROLLABLE = frozenset(
    e.strip() for e in os.environ.get("HA_CONTROLLABLE_ENTITIES", "").split(",")
    if e.strip())

# Re-read only when the file changes. The key uses nanosecond mtime and size,
# because two quick saves of the same length would otherwise look unchanged and
# a just-revoked permission would stay in force.
_policy_cache: tuple[tuple[int, int], frozenset[str]] | None = None


def controllable_entities() -> frozenset[str]:
    """Entities permitted on top of CONTROLLABLE_DOMAINS.

    A missing or broken file falls back to the environment rather than raising,
    because this runs on the refusal path. A bad edit should narrow
    permissions, not break the bridge.
    """
    global _policy_cache
    try:
        info = POLICY_FILE.stat()
        stamp = (info.st_mtime_ns, info.st_size)
    except OSError:
        return _ENV_CONTROLLABLE
    if _policy_cache is not None and _policy_cache[0] == stamp:
        return _policy_cache[1]
    try:
        data = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
        entities = data["controllable_entities"]
        value = frozenset(str(e) for e in entities) if isinstance(entities, list) \
            else _ENV_CONTROLLABLE
    except (OSError, ValueError, KeyError, TypeError):
        value = _ENV_CONTROLLABLE
    _policy_cache = (stamp, value)
    return value

# The opposite of the allowlist. An entity here is refused even if its domain
# is allowed, such as a light entity that controls something other than a
# light.
NOT_CONTROLLABLE = frozenset(
    e.strip() for e in os.environ.get("HA_DENIED_ENTITIES", "").split(",")
    if e.strip())


def entity_ids_in(config) -> set[str]:
    """Every entity id an automation mentions, at any depth, so each can be
    checked."""
    found: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("entity_id", "entity") and isinstance(value, str):
                    found.add(value)
                elif key in ("entity_id", "entity") and isinstance(value, list):
                    found.update(v for v in value if isinstance(v, str))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(config)
    return found


def is_controllable(entity_id: str) -> bool:
    """Whether this bridge may act on `entity_id`.

    The security-domain refusal wins over everything, then the deny list, then
    domain or entity permission. An allow never overrides a deny.
    """
    domain = (entity_id or "").split(".")[0]
    if not domain or domain in SECURITY_DOMAINS:
        return False
    if entity_id in NOT_CONTROLLABLE:
        return False
    return domain in CONTROLLABLE_DOMAINS or entity_id in controllable_entities()


def domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0] if "." in entity_id else ""


def require_controllable(entity_id: str, expected_domains: tuple[str, ...]) -> None:
    """Two independent refusals.

    The security-domain check comes first and ignores the allowlist, because
    the allowlist is the part most likely to be wrong.
    """
    if not entity_id or "." not in entity_id:
        raise BridgeError(400, f"not an entity id: {entity_id!r}")
    domain = domain_of(entity_id)

    if domain in SECURITY_DOMAINS:
        raise BridgeError(
            403, f"'{entity_id}' is in the {domain} domain, which this bridge "
                 f"never actuates. Locks, alarms and covers map to "
                 f"home_control_security, which is always_denied, so no approval "
                 f"exists for it and none can be issued.")

    if domain not in expected_domains:
        raise BridgeError(400, f"'{entity_id}' is a {domain}; this tool controls "
                               f"{' or '.join(expected_domains)}")

    if not is_controllable(entity_id):
        raise BridgeError(
            403, f"'{entity_id}' is not in the operator's controllable list. "
                 f"Reading it is fine; acting on it needs the operator to add "
                 f"it on the Operations page of the portal.")
