"""Home Assistant MCP — the assistant's view of the house.

Fronts homeassistant-bridge, which holds the long-lived token and enforces what
may be actuated. Seven tools, and none of them is a general `call_service`.

Writing an automation is the interesting one. An automation is stored code that
Home Assistant runs later with its own privileges, so every call-time refusal in
the bridge is irrelevant to it — an assistant that may not unlock a door could
otherwise schedule one for 3am. `automation.py` refuses templates outright so
that what an automation will do is decidable, and then checks it against the
same limits as a direct call.
"""
from __future__ import annotations

from mcp_base import ToolError, schema_object
from integrations._client import bridge_client

bridge_request = bridge_client("HA", "homeassistant-bridge", timeout=30)

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

    {"name": "list_home_automations", "title": "List automations you created",
     "description": "Automations you have written, which are prefixed so they "
                    "are distinguishable from the operator's own. Check here "
                    "before writing one, so you update rather than duplicate.",
     "inputSchema": schema_object({})},

    {"name": "create_home_automation", "title": "Write an automation",
     "description":
         "Create a Home Assistant automation that runs unattended from now on. "
         "Needs operator approval each time, because it is stored code that "
         "will run when nobody is watching.\n\n"
         "Hard constraints, refused rather than negotiated:\n"
         "- **No templates.** No {{ }} or {% %} anywhere. Every service name "
         "and entity id must be a literal, or the automation cannot be checked "
         "and is rejected.\n"
         "- It may only act on entities you could already control directly.\n"
         "- It may never touch locks, alarms, covers, cameras, or call "
         "shell_command, python_script, rest_command or homeassistant.turn_on.\n\n"
         "Triggers may reference anything, including sensors you cannot "
         "control — 'when the hall motion sensor fires' is the normal case. "
         "Give it a clear alias saying what it does; that is what the operator "
         "reads when deciding.",
     "inputSchema": schema_object({
         "automation": {
             "type": "object",
             "description": "The automation config: alias, trigger, optional "
                            "condition, and action. Example: {\"alias\": "
                            "\"Hall light on motion after sunset\", "
                            "\"trigger\": {\"platform\": \"state\", "
                            "\"entity_id\": \"binary_sensor.hall_motion\", "
                            "\"to\": \"on\"}, \"condition\": "
                            "{\"condition\": \"sun\", \"after\": "
                            "\"sunset\"}, \"action\": {\"service\": "
                            "\"light.turn_on\", \"entity_id\": "
                            "\"light.hall\"}}"}},
         ["automation"])},

    {"name": "look_at_camera", "title": "Check a room",
     "description":
         "Take one frame from an allowlisted camera and get back **structured "
         "facts**: how many people, their broad posture, and whether any text "
         "is visible. You never receive the image, and never a description in "
         "prose.\n\n"
         "Prefer presence sensors for 'which room is someone in' — "
         "list_home_entities with domain 'binary_sensor' answers that without "
         "looking at anyone. Use this when you need to know whether a room is "
         "occupied and roughly what is happening, and say that you are about "
         "to look.\n\n"
         "You cannot ask a free-text question, and text in the room is "
         "reported as present but never transcribed. Both limits exist because "
         "anyone who can put writing where the lens sees it would otherwise be "
         "able to address you through it.",
     "inputSchema": schema_object({
         "entity_id": {"type": "string", "description": "e.g. camera.kitchen"},
         "look_for": {"type": "string", "enum": ["occupancy", "activity"],
                      "default": "occupancy",
                      "description": "'occupancy' counts people; 'activity' "
                                     "also reports broad posture."}},
         ["entity_id"])},

    {"name": "cast_to_screen", "title": "Show something on a screen",
     "description":
         "Put a message on a screen or speaker. Screens have two privacy "
         "levels and you do not choose which applies:\n\n"
         "- **Shared** (living room television, kitchen display): only "
         "`summary` is shown. `detail` is discarded, not queued.\n"
         "- **Private** (an office monitor the operator has marked): both are "
         "shown.\n\n"
         "Write `summary` the way a phone writes a lock-screen preview — "
         "enough to know something is there, not enough to read over someone's "
         "shoulder. 'Calendar: 3 things today' rather than the appointments "
         "themselves. Put the actual content in `detail`; if the screen is "
         "shared it is dropped, and you should offer to say it in conversation "
         "instead.",
     "inputSchema": schema_object({
         "entity_id": {"type": "string",
                       "description": "e.g. media_player.living_room_tv"},
         "summary": {"type": "string", "maxLength": 80,
                     "description": "The preview. Shown on every screen."},
         "detail": {"type": "string",
                    "description": "The full content. Shown only on a screen "
                                   "the operator has marked private."}},
         ["entity_id", "summary"])},
]

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
    if name == "look_at_camera":
        return bridge_request("POST", "/v1/camera/look", payload={
            "entity_id": args["entity_id"],
            "look_for": args.get("look_for", "occupancy")})
    if name == "cast_to_screen":
        return bridge_request("POST", "/v1/cast", payload={
            "entity_id": args["entity_id"], "summary": args["summary"],
            "detail": args.get("detail", "")})
    if name == "list_home_automations":
        return bridge_request("GET", "/v1/automations")
    if name == "create_home_automation":
        return bridge_request("POST", "/v1/automations",
                              payload={"automation": args["automation"]})
    if name == "set_home_climate":
        return bridge_request("POST", "/v1/climate", payload={
            "entity_id": args["entity_id"], "temperature": args["temperature"]})
    raise ToolError(f"unknown tool: {name}")
