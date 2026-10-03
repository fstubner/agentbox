"""Calls to Home Assistant's REST API, with the token this bridge holds."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from bridge_base import BridgeError

HA_URL = os.environ.get("HA_URL", "").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
HTTP_TIMEOUT = int(os.environ.get("HA_TIMEOUT", "15"))


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
            # Never echo the upstream body, which can quote the token back.
            raise BridgeError(502, "Home Assistant rejected the credential") from None
        raise BridgeError(502, f"Home Assistant returned {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise BridgeError(502, f"Home Assistant unreachable: {exc.reason}") from None


def flatten(entity: dict) -> dict:
    """Lift the display name out of attributes so the lean view is useful."""
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


# --- acting -------------------------------------------------------------------


def call_service(domain: str, service: str, payload: dict) -> Any:
    return ha_request("POST", f"/api/services/{domain}/{service}", payload)
