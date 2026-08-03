#!/usr/bin/env python3
"""Home Assistant MCP — the assistant's view of the house.

Fronts homeassistant-bridge, which holds the long-lived token and enforces what
may be actuated. Five tools: two read, three act, and none of them is a general
`call_service`.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from mcp_base import McpHandler, ToolError, schema_object, serve

BRIDGE_URL = os.environ.get("HA_BRIDGE_URL", "http://homeassistant-bridge:8080")
BRIDGE_TOKEN = os.environ.get("HA_BRIDGE_TOKEN", "")

ENTITY_ID = {"type": "string",
             "description": "Home Assistant entity id, e.g. light.kitchen."}

TOOLS = [
    {"name": "list_home_entities", "title": "List things in the house",
     "description":
         "List entities and their current state — lights, sensors, switches, "
         "climate. Filter by domain to keep it small; a house has hundreds of "
         "entities. The response also lists which entities you are permitted "
         "to control, so check that before offering to change something.",
     "inputSchema": schema_object({
         "domain": {"type": "string",
                    "description": "e.g. 'light', 'sensor', 'climate', 'scene'."},
         "view": {"type": "string", "enum": ["lean", "full"], "default": "lean",
                  "description": "'lean' gives id, state and name and is almost "
                                 "always what you want. 'full' adds every "
                                 "attribute and is very large."},
         "limit": {"type": "integer", "minimum": 1, "maximum": 1000,
                   "default": 100}})},

    {"name": "get_home_entity", "title": "Read one entity",
     "description": "Full current state and attributes for a single entity.",
     "inputSchema": schema_object({"entity_id": ENTITY_ID}, ["entity_id"])},

    {"name": "set_home_light", "title": "Turn a light or switch on or off",
     "description":
         "Turn an allowlisted light or switch on or off, optionally with a "
         "brightness. Only entities the operator has marked controllable will "
         "work; anything else is refused, and so is anything in a lock, alarm "
         "or cover domain regardless of the list.",
     "inputSchema": schema_object({
         "entity_id": ENTITY_ID,
         "on": {"type": "boolean"},
         "brightness_pct": {"type": "integer", "minimum": 1, "maximum": 100,
                            "description": "Lights only. Omit to leave "
                                           "brightness unchanged."}},
         ["entity_id", "on"])},

    {"name": "activate_home_scene", "title": "Activate a scene or script",
     "description": "Activate an allowlisted scene or script.",
     "inputSchema": schema_object({"entity_id": ENTITY_ID}, ["entity_id"])},

    {"name": "set_home_climate", "title": "Set a target temperature",
     "description":
         "Set the target temperature on an allowlisted thermostat. This needs "
         "operator approval each time — heating costs money and a household "
         "may be asleep — so expect it to be refused until one is granted, and "
         "say what you are trying to do rather than retrying.",
     "inputSchema": schema_object({
         "entity_id": ENTITY_ID,
         "temperature": {"type": "number", "minimum": 5, "maximum": 30,
                         "description": "Target in °C."}},
         ["entity_id", "temperature"])},
]


def bridge_request(method, path, payload=None, query=None):
    if not BRIDGE_TOKEN:
        raise ToolError("HA_BRIDGE_TOKEN is not configured")
    url = f"{BRIDGE_URL}{path}"
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items()
                                             if v not in (None, "")})
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {BRIDGE_TOKEN}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        try:
            detail = json.loads(detail).get("error", detail)
        except ValueError:
            pass
        raise ToolError(detail)
    except urllib.error.URLError as exc:
        raise ToolError(f"home assistant bridge unreachable: {exc.reason}")


def dispatch(name, args):
    if name == "list_home_entities":
        return bridge_request("GET", "/v1/entities", query={
            "domain": args.get("domain", ""),
            "view": args.get("view", "lean"),
            "limit": args.get("limit", 100)})
    if name == "get_home_entity":
        return bridge_request("GET", "/v1/entity",
                              query={"entity_id": args["entity_id"]})
    if name == "set_home_light":
        payload = {"entity_id": args["entity_id"], "on": args["on"]}
        if args.get("brightness_pct") is not None:
            payload["brightness_pct"] = args["brightness_pct"]
        return bridge_request("POST", "/v1/light", payload=payload)
    if name == "activate_home_scene":
        return bridge_request("POST", "/v1/scene",
                              payload={"entity_id": args["entity_id"]})
    if name == "set_home_climate":
        return bridge_request("POST", "/v1/climate", payload={
            "entity_id": args["entity_id"], "temperature": args["temperature"]})
    raise ToolError(f"unknown tool: {name}")


class HomeAssistantMcp(McpHandler):
    service_name = "homeassistant-mcp"
    instructions = ("Read and control the house through Home Assistant. "
                    "Control is limited to entities the operator has "
                    "allowlisted; locks, alarms and covers are never actuated.")
    tools = TOOLS
    dispatch = staticmethod(dispatch)
    bridge_url = BRIDGE_URL
    shared_token = os.environ.get("HA_MCP_SHARED_TOKEN", "")


if __name__ == "__main__":
    serve(HomeAssistantMcp)
