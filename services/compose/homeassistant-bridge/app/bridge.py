#!/usr/bin/env python3
"""Home Assistant bridge — the house, behind a narrow contract.

Holds a long-lived HA access token and exposes a small allowlisted API. The
assistant never sees the token and never gets `call_service`.

## Why there is no general call_service

Home Assistant's REST API is one endpoint away from total control of the house:
`POST /api/services/<domain>/<service>` will unlock a door as readily as it
turns on a lamp. Exposing that and putting an approval in front of it would be
the wrong shape — the platform's rule is **constrain rather than gate**, because
a constraint holds when the model is compromised and an approval only helps if
a human reads carefully first. An approval prompt that appears every time
somebody asks for a light is one that gets granted unread within a week, and by
then it is granting nothing.

So control is three narrow tools over an **operator-configured entity
allowlist**, and two independent refusals underneath:

- an entity absent from `HA_CONTROLLABLE_ENTITIES` is refused;
- an entity in a security domain is refused **even if allowlisted**, because
  that list is edited by a tired human and `lock.front_door` looks a lot like
  `light.front_door` at the end of a long day.

Locks, alarms, garage doors and covers map to `home_control_security`, which is
`always_denied` — there is no grant that unlocks a door, and this service could
not act on one if there were.

Reads are unrestricted, deliberately: knowing the kitchen is 19°C is not a
capability worth gating, and a sensor allowlist would need editing every time a
battery is replaced.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from bridge_base import (BridgeError, BridgeHandler, project_fields,
                         resolve_limit, resolve_view, serve)

HA_URL = os.environ.get("HA_URL", "").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
HTTP_TIMEOUT = int(os.environ.get("HA_TIMEOUT", "15"))

# Domains this service will never act on, whatever the allowlist says. Not a
# policy tier — a hard refusal in the process that holds the credential, so a
# mis-edited allowlist cannot open a door.
SECURITY_DOMAINS = frozenset({"lock", "alarm_control_panel", "cover",
                              "garage_door", "vacuum", "camera"})

# Comma-separated entity ids the operator has decided are safe to control.
# Empty means control nothing, which is the right default for a service that
# can act on the physical world.
CONTROLLABLE = frozenset(
    e.strip() for e in os.environ.get("HA_CONTROLLABLE_ENTITIES", "").split(",")
    if e.strip())

LEAN_FIELDS = ("entity_id", "state", "friendly_name")


def ha_request(method: str, path: str, payload: dict | None = None) -> Any:
    if not HA_URL or not HA_TOKEN:
        raise BridgeError(503, "Home Assistant is not configured "
                               "(HA_URL / HA_TOKEN unset)")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {HA_TOKEN}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{HA_URL}{path}", data=data,
                                     method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code in (401, 403):
            # Never echo the upstream body here: an auth failure from HA can
            # quote the token back.
            raise BridgeError(502, "Home Assistant rejected the credential")
        raise BridgeError(502, f"Home Assistant returned {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        raise BridgeError(502, f"Home Assistant unreachable: {exc.reason}")


def domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0] if "." in entity_id else ""


def require_controllable(entity_id: str, expected_domains: tuple[str, ...]) -> None:
    """Two refusals, deliberately independent.

    The security-domain check is first and unconditional. It does not consult
    the allowlist, because the allowlist is the thing most likely to be wrong —
    `lock.front_door` and `light.front_door` differ by two characters.
    """
    if not entity_id or "." not in entity_id:
        raise BridgeError(400, f"not an entity id: {entity_id!r}")
    domain = domain_of(entity_id)

    if domain in SECURITY_DOMAINS:
        raise BridgeError(
            403, f"'{entity_id}' is in the {domain} domain, which this bridge "
                 f"never actuates. Locks, alarms and covers map to "
                 f"home_control_security, which is always_denied — no approval "
                 f"exists for it and none can be issued.")

    if domain not in expected_domains:
        raise BridgeError(400, f"'{entity_id}' is a {domain}; this tool controls "
                               f"{' or '.join(expected_domains)}")

    if entity_id not in CONTROLLABLE:
        raise BridgeError(
            403, f"'{entity_id}' is not in the operator's controllable list. "
                 f"Reading it is fine; acting on it needs the operator to add "
                 f"it to HA_CONTROLLABLE_ENTITIES.")


def flatten(entity: dict) -> dict:
    """HA nests the display name under attributes; lift it so lean is useful."""
    attributes = entity.get("attributes") or {}
    flat = {
        "entity_id": entity.get("entity_id"),
        "state": entity.get("state"),
        "friendly_name": attributes.get("friendly_name"),
        "last_changed": entity.get("last_changed"),
        "attributes": attributes,
    }
    return {k: v for k, v in flat.items() if v is not None}


def query_of(handler) -> dict:
    return urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)


def first(query, key, default=""):
    value = query.get(key, [default])
    return value[0] if value else default


# --- reading ------------------------------------------------------------------


def list_entities(handler, body):
    """All entity states, optionally filtered by domain.

    A house produces a lot of entities — a modest setup is several hundred, and
    the full payload with attributes is hundreds of kilobytes. `lean` is the
    default here rather than `full`, which is the opposite of the other
    bridges: nobody asking "is the kitchen light on" wants a device registry
    entry, and the full view is large enough to be a context-economy problem on
    its own.
    """
    query = query_of(handler)
    view = resolve_view(first(query, "view", "lean"))
    domain = first(query, "domain", "").strip()
    limit = resolve_limit(first(query, "limit", ""), default=100, maximum=1000)

    entities = ha_request("GET", "/api/states") or []
    flattened = [flatten(e) for e in entities if isinstance(e, dict)]
    if domain:
        flattened = [e for e in flattened
                     if domain_of(e.get("entity_id", "")) == domain]
    flattened.sort(key=lambda e: e.get("entity_id") or "")
    total = len(flattened)
    page = flattened[:limit]
    if view == "lean":
        page = project_fields(page, LEAN_FIELDS)
    return 200, {"entities": page, "total": total, "returned": len(page),
                 "controllable": sorted(CONTROLLABLE)}


def get_entity(handler, body):
    query = query_of(handler)
    entity_id = first(query, "entity_id").strip()
    if not entity_id or "." not in entity_id:
        raise BridgeError(400, "entity_id is required, e.g. light.kitchen")
    entity = ha_request("GET", f"/api/states/{urllib.parse.quote(entity_id)}")
    if not entity:
        raise BridgeError(404, f"no such entity: {entity_id}")
    return 200, flatten(entity)


# --- acting -------------------------------------------------------------------


def call_service(domain: str, service: str, payload: dict) -> Any:
    return ha_request("POST", f"/api/services/{domain}/{service}", payload)


def set_light(handler, body):
    body = body or {}
    entity_id = str(body.get("entity_id") or "").strip()
    require_controllable(entity_id, ("light", "switch"))
    on = body.get("on")
    if not isinstance(on, bool):
        raise BridgeError(400, "on must be true or false")

    domain = domain_of(entity_id)
    payload: dict[str, Any] = {"entity_id": entity_id}
    if on and domain == "light":
        brightness = body.get("brightness_pct")
        if brightness is not None:
            try:
                brightness = int(brightness)
            except (TypeError, ValueError):
                raise BridgeError(400, "brightness_pct must be an integer")
            if not 1 <= brightness <= 100:
                raise BridgeError(400, "brightness_pct must be 1-100")
            payload["brightness_pct"] = brightness
    call_service(domain, "turn_on" if on else "turn_off", payload)
    return 200, {"entity_id": entity_id, "requested": "on" if on else "off",
                 "note": "Home Assistant accepted the command; it does not "
                         "confirm the bulb responded."}


def activate_scene(handler, body):
    body = body or {}
    entity_id = str(body.get("entity_id") or "").strip()
    require_controllable(entity_id, ("scene", "script"))
    call_service(domain_of(entity_id), "turn_on", {"entity_id": entity_id})
    return 200, {"entity_id": entity_id, "requested": "activate"}


def set_climate(handler, body):
    body = body or {}
    entity_id = str(body.get("entity_id") or "").strip()
    require_controllable(entity_id, ("climate",))
    try:
        temperature = float(body.get("temperature"))
    except (TypeError, ValueError):
        raise BridgeError(400, "temperature is required and must be a number")
    # A bound the assistant cannot argue its way past. Not comfort policy — a
    # thermostat driven to an extreme by a confused model or an injected
    # instruction is a burst pipe or a heat risk to whoever is asleep upstairs.
    if not 5 <= temperature <= 30:
        raise BridgeError(400, "temperature must be between 5 and 30 °C")
    call_service("climate", "set_temperature",
                 {"entity_id": entity_id, "temperature": temperature})
    return 200, {"entity_id": entity_id, "temperature": temperature}


def get_schema(handler, body):
    return 200, {
        "routes": ["GET /v1/entities", "GET /v1/entity",
                   "POST /v1/light", "POST /v1/scene", "POST /v1/climate"],
        "views": {"lean": list(LEAN_FIELDS), "full": "adds attributes"},
        "controllable": sorted(CONTROLLABLE),
        "never_actuated": sorted(SECURITY_DOMAINS),
        "cannot": ["call arbitrary services", "actuate locks, alarms or covers",
                   "control an entity outside the operator's allowlist"],
    }


class HomeAssistantBridge(BridgeHandler):
    server_version = "homeassistant-bridge/1.0"
    bridge_token = os.environ.get("HA_BRIDGE_TOKEN", "")
    routes = {
        ("GET", "/schema"): get_schema,
        ("GET", "/v1/entities"): list_entities,
        ("GET", "/v1/entity"): get_entity,
        ("POST", "/v1/light"): set_light,
        ("POST", "/v1/scene"): activate_scene,
        ("POST", "/v1/climate"): set_climate,
    }

    def capability_for(self, method: str, path: str,
                       body: dict[str, Any] | None) -> str | None:
        if path.startswith("/v1/entities") or path.startswith("/v1/entity"):
            return "home_read_state"
        if path.startswith("/v1/climate"):
            return "home_control_climate"
        if path.startswith("/v1/light") or path.startswith("/v1/scene"):
            return "home_control_comfort"
        return None

    def upstream_status(self) -> dict[str, Any]:
        if not HA_URL or not HA_TOKEN:
            return {"ok": False, "upstream": {"error": "HA_URL / HA_TOKEN unset"}}
        try:
            ha_request("GET", "/api/")
        except BridgeError as exc:
            return {"ok": False, "upstream": {"url": HA_URL, "error": exc.message}}
        return {"ok": True, "upstream": {"url": HA_URL,
                                         "controllable": len(CONTROLLABLE)}}


if __name__ == "__main__":
    serve(HomeAssistantBridge)
