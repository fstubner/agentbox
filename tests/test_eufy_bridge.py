"""Eufy bridge: viewing constraints, and no route that actuates anything.

Two of these cameras are indoors and one is a doorbell, so the most important
tests are the ones about what cannot be reached.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from conftest import code_of

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "services" / "compose" / "eufy-bridge" / "app"


@pytest.fixture
def eufy(monkeypatch):
    monkeypatch.setenv("EUFY_VIEWABLE_CAMERAS", "T8210N,DOORBELL1")
    sys.path.insert(0, str(APP))
    sys.path.insert(0, str(REPO / "services" / "templates" / "bridge" / "app"))
    for name in ("bridge", "bridge_base", "wsclient"):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location("bridge", APP / "bridge.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["bridge"] = module
    spec.loader.exec_module(module)
    return module


# --- what cannot be reached ----------------------------------------------------


def test_there_is_no_route_that_actuates_anything(eufy):
    """There is no route to gate. If the assistant could arm a camera, an
    injected assistant could disarm it, and a policy tier can be edited."""
    joined = " ".join(eufy._POST_ROUTES)
    for forbidden in ("arm", "disarm", "alarm", "pan", "tilt", "record",
                      "lock", "unlock", "reboot", "delete"):
        assert forbidden not in joined

    source = code_of(APP / "bridge.py")
    for command in ("station.set_guard_mode", "device.start_rtsp",
                    "station.trigger_alarm", "device.unlock"):
        assert command not in source


def test_only_two_routes_exist(eufy):
    """Viewing and listing. Adding a route means changing this test, so a
    new route is a visible decision."""
    assert set(eufy._POST_ROUTES) == {"/v1/eufy/devices", "/v1/eufy/snapshot"}


# --- viewing ------------------------------------------------------------------


def test_a_camera_outside_the_list_cannot_be_viewed(eufy):
    with pytest.raises(eufy.BridgeError) as exc:
        eufy.require_viewable("INDOOR_BEDROOM")
    assert exc.value.status == 403


def test_an_empty_list_views_nothing(monkeypatch):
    """Indoor cameras make this the only safe default."""
    monkeypatch.setenv("EUFY_VIEWABLE_CAMERAS", "")
    sys.path.insert(0, str(APP))
    for name in ("bridge", "wsclient"):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location("bridge2", APP / "bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.VIEWABLE == frozenset()
    with pytest.raises(module.BridgeError):
        module.require_viewable("T8210N")


def test_an_allowlisted_camera_passes(eufy):
    eufy.require_viewable("T8210N")


def test_a_missing_serial_is_refused_before_the_allowlist(eufy):
    with pytest.raises(eufy.BridgeError) as exc:
        eufy.require_viewable("")
    assert exc.value.status == 400


# --- untrusted content ---------------------------------------------------------


def test_frames_are_labelled_untrusted(eufy, monkeypatch):
    """Anything written in view of the lens, such as a note on a fridge, can
    carry an instruction."""
    monkeypatch.setattr(eufy, "command", lambda *a, **k: {
        "value": {"data": "BASE64", "type": "image/jpeg"}})
    result = eufy.snapshot({"serial": "T8210N"})
    assert "untrusted_image_base64" in result
    assert not [k for k in result if k in ("image", "image_base64", "picture")]


def test_listing_devices_does_not_leak_state(eufy, monkeypatch):
    """The list says which cameras exist. It does not say whether a camera
    currently sees someone, which is a separate disclosure."""
    monkeypatch.setattr(eufy, "command", lambda *a, **k: {"devices": [
        {"serialNumber": "T8210N", "name": "Front", "model": "T8210",
         "type": 7, "motionDetected": True, "person": "Alex"}]})
    device = eufy.list_devices({})["devices"][0]
    assert set(device) == {"serial", "name", "model", "type", "viewable"}


# --- correlation ---------------------------------------------------------------


def test_replies_are_matched_by_message_id(eufy):
    """The socket also carries unsolicited device events. Taking the next
    message off it could return an event instead of the reply."""
    import inspect
    source = inspect.getsource(eufy.command)
    assert 'message.get("messageId") == message_id' in source


def test_a_dropped_connection_reconnects_rather_than_staying_broken(eufy):
    import inspect
    source = inspect.getsource(eufy.command)
    assert "drop_client()" in source


# --- the websocket client ------------------------------------------------------


def test_client_frames_are_masked_with_a_fresh_mask():
    """RFC 6455 requires client frames be masked with an unpredictable mask.
    Some servers accept a fixed mask, but proxies reject it."""
    source = code_of(APP / "wsclient.py")
    assert "secrets.token_bytes(4)" in source
    assert "0x80 | TEXT" in source


def test_oversized_frames_are_refused():
    """This process has a memory limit. With an 8 MB cap an oversized frame
    is refused, instead of an OOM kill taking the bridge down."""
    source = code_of(APP / "wsclient.py")
    assert "frame too large" in source


def test_only_plaintext_ws_to_a_private_network():
    source = code_of(APP / "wsclient.py")
    assert 'parsed.scheme != "ws"' in source
