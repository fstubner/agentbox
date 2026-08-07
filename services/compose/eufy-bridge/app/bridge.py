#!/usr/bin/env python3
"""Eufy Security, behind the same policy surface as everything else.

## Why this exists rather than the Home Assistant integration

Eufy publishes no third-party API, so every route to these cameras is
reverse-engineered. The usual one is a HACS integration installed *inside* Home
Assistant — and this deployment's Home Assistant already runs with host
networking, a system D-Bus socket, NET_ADMIN and NET_RAW, reachable from the
LAN. Adding unreviewed third-party code to the most privileged container on the
box, to reach the cameras specifically, is the worst placement available.

So the reverse-engineered part stays where it belongs: `eufy-security-ws` runs
as its own container on a private network with no host ports, and this bridge
talks to it. If that code misbehaves it has a docker network and a Eufy
credential, not a D-Bus socket and a Bluetooth radio.

What is *not* being done here is reimplementing Eufy's protocol.
eufy-security-client carries four of them — an HTTPS cloud API, an undocumented
UDP P2P stack that also carries video, Firebase push, and MQTT for locks.
Rewriting that is months of packet capture and breaks on every firmware
release. Consuming it from an isolated container is the good trade.

## What this bridge will and will not do

Looking is allowed, on request, at allowlisted cameras only. Acting is not.

There is no arm, disarm, alarm trigger, pan, tilt, recording start or lock
route, and their absence is the point — not a tier that could be granted later
by editing a policy file. A camera the assistant can arm is a camera an
injected assistant can disarm.
"""
from __future__ import annotations

import os
import threading
import time

from bridge_base import BridgeError, BridgeHandler, serve
from wsclient import WebSocketError, connect

WS_URL = os.environ.get("EUFY_WS_URL", "ws://eufy-security-ws:3000")
BRIDGE_TOKEN = os.environ.get("EUFY_BRIDGE_TOKEN", "")
HOST = os.environ.get("BRIDGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("BRIDGE_PORT", "8080"))
SCHEMA_VERSION = int(os.environ.get("EUFY_SCHEMA_VERSION", "21"))

# Serial numbers the operator has decided may be looked at. Empty means look at
# nothing, which is the right default for indoor cameras: the failure direction
# of a missing config must be less access, never more.
VIEWABLE = frozenset(
    s.strip() for s in os.environ.get("EUFY_VIEWABLE_CAMERAS", "").split(",")
    if s.strip())

_client = None
_lock = threading.Lock()


def client():
    """One shared connection, re-established on failure.

    eufy-security-ws drops clients on its own restarts and on Eufy cloud
    hiccups, and a bridge that returns 500 until someone notices is a bridge
    that looks broken rather than reconnecting.
    """
    global _client
    with _lock:
        if _client is not None:
            return _client
        try:
            socket = connect(WS_URL)
            socket.send_json({"messageId": "schema",
                              "command": "set_api_schema",
                              "schemaVersion": SCHEMA_VERSION})
            socket.recv()
            socket.send_json({"messageId": "start",
                              "command": "start_listening"})
            socket.recv()
        except (WebSocketError, OSError) as exc:
            raise BridgeError(503, f"eufy-security-ws unreachable: "
                                   f"{type(exc).__name__}") from None
        _client = socket
        return _client


def drop_client():
    global _client
    with _lock:
        if _client is not None:
            _client.close()
        _client = None


def command(payload: dict, timeout: float = 20.0) -> dict:
    """Send one command and wait for its reply.

    Replies are correlated by messageId rather than by taking the next message
    off the socket: this connection also carries unsolicited device events, and
    reading one of those as a command result would silently answer the wrong
    question.
    """
    message_id = f"m{int(time.time() * 1000)}"
    payload = {**payload, "messageId": message_id}
    socket = client()
    try:
        socket.send_json(payload)
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = socket.recv()
            if message is None:
                break
            if message.get("messageId") == message_id:
                if not message.get("success", True):
                    detail = str(message.get("errorCode", "unknown"))
                    raise BridgeError(502, {"eufy_error": detail})
                return message.get("result", {}) or {}
        raise BridgeError(504, "eufy-security-ws did not answer in time")
    except (WebSocketError, OSError):
        drop_client()
        raise BridgeError(503, "lost the connection to eufy-security-ws; "
                               "it will reconnect on the next call") from None


def require_viewable(serial: str) -> None:
    if not serial:
        raise BridgeError(400, "serial is required")
    if serial not in VIEWABLE:
        raise BridgeError(
            403, f"'{serial}' is not in the operator's viewable camera list. "
                 f"Indoor cameras are not viewable by default, and adding one "
                 f"is a decision for a person, not a tool call.")


def list_devices(_body):
    """Everything the account can see, as metadata only.

    Deliberately no state values in this response. Knowing a camera exists is a
    different disclosure from knowing whether it currently sees someone.
    """
    result = command({"command": "driver.get_devices"})
    devices = []
    for device in result.get("devices", []) or []:
        serial = device.get("serialNumber", "")
        devices.append({
            "serial": serial,
            "name": device.get("name", ""),
            "model": device.get("model", ""),
            "type": device.get("type", ""),
            "viewable": serial in VIEWABLE,
        })
    return {"devices": devices, "total": len(devices),
            "viewable": sorted(VIEWABLE)}


def snapshot(body):
    """One still frame from an allowlisted camera.

    The returned image is untrusted input with a *physical* attack surface:
    anything in view of the lens can carry text — a note on a fridge, a phone
    screen, a television. Named so the caller cannot forget that.
    """
    serial = str(body.get("serial", "")).strip()
    require_viewable(serial)
    result = command({"command": "device.get_property",
                      "serialNumber": serial, "name": "picture"})
    picture = (result.get("value") or {}) if isinstance(result.get("value"), dict) else {}
    return {
        "serial": serial,
        "untrusted_image_base64": picture.get("data", ""),
        "content_type": picture.get("type", "image/jpeg"),
        "captured": "most recent frame the camera has published",
    }


_POST_ROUTES = {
    "/v1/eufy/devices": list_devices,
    "/v1/eufy/snapshot": snapshot,
}


def _wrap(handler, path):
    def run(body):
        return handler(body)
    run.__name__ = path
    return run


class Handler(BridgeHandler):
    token = BRIDGE_TOKEN

    def capability_for(self, method, path, body):
        if path == "/v1/eufy/snapshot":
            return "home_view_camera"
        if path == "/v1/eufy/devices":
            return "home_read_state"
        return None

    def upstream_status(self):
        try:
            command({"command": "driver.is_connected"}, timeout=8)
            return {"ok": True, "upstream": {"eufy_ws": "connected"}}
        except BridgeError as exc:
            return {"ok": False, "upstream": {"eufy_ws": str(exc.message)[:120]}}

    routes = {("POST", path): _wrap(fn, path)
              for path, fn in _POST_ROUTES.items()}


if __name__ == "__main__":
    serve(Handler, HOST, PORT)
