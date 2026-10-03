#!/usr/bin/env python3
"""The Home Assistant bridge: the house, behind a narrow contract.

It holds a long-lived Home Assistant token and exposes a small API. The
assistant never sees the token and never gets `call_service`.

## Why there is no general call_service

`POST /api/services/<domain>/<service>` unlocks a door as readily as it turns
on a lamp. An approval in front of that would be asked every time someone
wanted a light, and would soon be granted without reading. So control is a few
narrow tools with two independent refusals underneath.

- An entity not on the household's allowlist is refused. The list lives on the
  read-only policy mount and is edited from the portal's Operations page, never
  by this container or the assistant.
- An entity in a security domain is refused even if it is on the list, because
  `lock.front_door` and `light.front_door` are easy to confuse.

Locks, alarms, garage doors and covers map to `home_control_security`, which is
`always_denied`. No grant unlocks a door, and this service could not act on one
if it existed.

Reads are unrestricted. Knowing the kitchen is 19 °C is not worth gating.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import automation
from bridge_base import BridgeError, BridgeHandler, project_fields, resolve_limit, resolve_view, serve
from ha_access import (  # noqa: F401
    SECURITY_DOMAINS,
    controllable_entities,
    domain_of,
    entity_ids_in,
    is_controllable,
    require_controllable,
)
from ha_api import (  # noqa: F401
    HA_TOKEN,
    HA_URL,
    call_service,
    first,
    flatten,
    ha_request,
    query_of,
)
from ha_cameras import (  # noqa: F401
    LOOK_PROMPTS,
    MAX_PEOPLE,
    coerce_observation,
    look_at_camera,
)
from ha_screens import (  # noqa: F401
    cast,
)

# Every automation this service writes starts with this prefix, so the owner
# can see in Home Assistant which ones the assistant wrote and remove them.
AUTOMATION_ALIAS_PREFIX = os.environ.get("HA_AUTOMATION_PREFIX", "[agentbox] ")

LEAN_FIELDS = ("entity_id", "state", "friendly_name")


# --- reading ------------------------------------------------------------------


def list_entities(handler, body):
    """All entity states, optionally filtered by domain.

    `lean` is the default here, unlike the other bridges. Even a modest house
    has hundreds of entities, the full payload runs to hundreds of kilobytes,
    and nobody asking whether the kitchen light is on wants all of it.
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
    # Computed from what exists rather than echoing the config, since a
    # domain rule permits entities nobody listed.
    controllable = sorted(e["entity_id"] for e in flattened
                          if is_controllable(e.get("entity_id", "")))
    return 200, {"entities": page, "total": total, "returned": len(page),
                 "controllable": controllable}


def get_entity(handler, body):
    query = query_of(handler)
    entity_id = first(query, "entity_id").strip()
    if not entity_id or "." not in entity_id:
        raise BridgeError(400, "entity_id is required, e.g. light.kitchen")
    entity = ha_request("GET", f"/api/states/{urllib.parse.quote(entity_id)}")
    if not entity:
        raise BridgeError(404, f"no such entity: {entity_id}")
    return 200, flatten(entity)


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
                raise BridgeError(400, "brightness_pct must be an integer") from None
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
        raise BridgeError(400, "temperature is required and must be a number") from None
    # A hard limit. A thermostat pushed to an extreme by a confused model or
    # an injected instruction risks a burst pipe or someone overheating.
    if not 5 <= temperature <= 30:
        raise BridgeError(400, "temperature must be between 5 and 30 °C")
    call_service("climate", "set_temperature",
                 {"entity_id": entity_id, "temperature": temperature})
    return 200, {"entity_id": entity_id, "temperature": temperature}


def list_automations(handler, body):
    """Automations this service wrote, identified by the alias prefix."""
    entities = ha_request("GET", "/api/states") or []
    ours = [flatten(e) for e in entities
            if isinstance(e, dict)
            and domain_of(e.get("entity_id", "")) == "automation"
            and str((e.get("attributes") or {}).get("friendly_name", ""))
            .startswith(AUTOMATION_ALIAS_PREFIX)]
    return 200, {"automations": project_fields(ours, LEAN_FIELDS),
                 "total": len(ours), "prefix": AUTOMATION_ALIAS_PREFIX}


def create_automation(handler, body):
    """Write an automation, after proving it cannot reach anything forbidden.

    An automation is stored code that Home Assistant runs later with its own
    privileges, so the per-call checks do not cover it. Without validation, an
    assistant that may not unlock a door could schedule one.
    """
    body = body or {}
    config = body.get("automation")
    if not isinstance(config, dict):
        raise BridgeError(400, "automation must be an object")

    try:
        # Checked against the same rule as a live call, including domain
        # permission, so anything the assistant may switch now can also go in
        # an automation.
        allowed = {e for e in entity_ids_in(config) if is_controllable(e)}
        summary = automation.validate(json.dumps(config), config, allowed)
    except automation.AutomationRefused as exc:
        raise BridgeError(403, str(exc)) from None

    alias = str(config.get("alias") or "").strip()
    if not alias:
        raise BridgeError(400, "automation needs an alias describing what it does")
    if not alias.startswith(AUTOMATION_ALIAS_PREFIX):
        alias = AUTOMATION_ALIAS_PREFIX + alias
    config = {**config, "alias": alias}

    # Home Assistant keys automations by id. Deriving it from the alias means
    # proposing the same automation again updates it rather than adding a copy.
    import hashlib
    automation_id = "agentbox_" + hashlib.sha256(
        alias.encode("utf-8")).hexdigest()[:16]

    ha_request("POST", f"/api/config/automation/config/{automation_id}", config)
    return 201, {
        "id": automation_id,
        "alias": alias,
        **summary,
        "note": "Created and enabled in Home Assistant. Delete it there, or "
                "ask the operator to.",
    }


def get_schema(handler, body):
    return 200, {
        "routes": ["GET /v1/entities", "GET /v1/entity",
                   "POST /v1/light", "POST /v1/scene", "POST /v1/climate",
                   "GET /v1/automations", "POST /v1/automations"],
        "views": {"lean": list(LEAN_FIELDS), "full": "adds attributes"},
        "controllable": sorted(controllable_entities()),
        "never_actuated": sorted(SECURITY_DOMAINS),
        "cannot": ["call arbitrary services", "actuate locks, alarms or covers",
                   "control an entity outside the operator's allowlist",
                   "write an automation containing templates",
                   "write an automation reaching anything it cannot call"],
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
        ("GET", "/v1/automations"): list_automations,
        ("POST", "/v1/automations"): create_automation,
        ("POST", "/v1/camera/look"): look_at_camera,
        ("POST", "/v1/cast"): cast,
    }

    def capability_for(self, method: str, path: str,
                       body: dict[str, Any] | None) -> str | None:
        if path.startswith(("/v1/entities", "/v1/entity")):
            return "home_read_state"
        if path.startswith("/v1/climate"):
            return "home_control_climate"
        if path.startswith(("/v1/light", "/v1/scene")):
            return "home_control_comfort"
        if path.startswith("/v1/camera"):
            return "home_view_camera"
        if path.startswith("/v1/cast"):
            return "home_control_comfort"
        if path.startswith("/v1/automations"):
            # Writing one is approval_required. Validation proves it cannot
            # reach a lock, but not whether it is a good idea, and code that
            # runs unattended deserves a person reading it once.
            return ("home_write_automation" if method == "POST"
                    else "home_read_state")
        return None

    def upstream_status(self) -> dict[str, Any]:
        if not HA_URL or not HA_TOKEN:
            return {"ok": False, "upstream": {"error": "HA_URL / HA_TOKEN unset"}}
        try:
            ha_request("GET", "/api/")
        except BridgeError as exc:
            return {"ok": False, "upstream": {"url": HA_URL, "error": exc.message}}
        return {"ok": True, "upstream": {"url": HA_URL,
                                         "controllable": len(controllable_entities())}}


if __name__ == "__main__":
    serve(HomeAssistantBridge)
