"""Tests for calendar event projection.

calendar/events is the largest payload in the platform — 23 KB for ten events,
~28x the full vikunja task list. These guard the properties the saving depends
on: the projection must not invent or alter data, must keep the fields a
scheduling answer needs, and `full` must remain the default so enabling the
parameter cannot silently change what the assistant already sees.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from conftest import patch_everywhere

REPO = Path(__file__).resolve().parent.parent
BASE_APP = REPO / "services" / "templates" / "bridge" / "app"
BRIDGE = REPO / "services" / "compose" / "google-workspace-bridge" / "app" / "bridge.py"

sys.path.insert(0, str(BASE_APP))
spec = importlib.util.spec_from_file_location("google_bridge", BRIDGE)
gb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gb)


FULL_EVENT = {
    "id": "abc123", "summary": "Budget review", "status": "confirmed",
    "start": {"dateTime": "2026-08-03T15:00:00Z"},
    "end": {"dateTime": "2026-08-03T16:00:00Z"},
    "location": "Room 2",
    "attendees": [{"email": "a@example.com"}, {"email": "b@example.com"}],
    "htmlLink": "https://calendar.google.com/event?eid=abc123",
    "creator": {"email": "a@example.com"}, "organizer": {"email": "a@example.com"},
    "iCalUID": "abc123@google.com", "etag": '"123"', "kind": "calendar#event",
    "created": "2026-07-01T00:00:00Z", "updated": "2026-07-02T00:00:00Z",
}

UPSTREAM = {
    "kind": "calendar#events", "etag": '"x"', "summary": "alex@example.com",
    "description": "chatty calendar description", "updated": "2026-08-01T00:00:00Z",
    "timeZone": "Europe/Dublin", "accessRole": "owner",
    "defaultReminders": [{"method": "popup", "minutes": 10}],
    "nextPageToken": "PAGE2",
    "items": [FULL_EVENT],
}


@pytest.fixture(autouse=True)
def stub_google(monkeypatch):
    """Return the fixture instead of calling Google."""
    patch_everywhere(monkeypatch, gb, "google_json", lambda *a, **k: UPSTREAM)


def test_default_view_is_full():
    result = gb.calendar_events({})
    assert result == UPSTREAM


def test_unknown_view_rejected():
    with pytest.raises(gb.BridgeError) as exc:
        gb.calendar_events({"view": "tiny"})
    assert exc.value.status == 400


def test_lean_keeps_only_scheduling_fields():
    event = gb.calendar_events({"view": "lean"})["items"][0]
    assert set(event) == {"id", "summary", "start", "end", "location", "status"}


def test_lean_preserves_values_exactly():
    event = gb.calendar_events({"view": "lean"})["items"][0]
    for key, value in event.items():
        assert value == FULL_EVENT[key]


def test_lean_keeps_status_so_cancelled_events_stay_distinguishable():
    """Dropping status would make a cancelled event look live — an accuracy
    loss, not a saving."""
    assert "status" in gb.LEAN_EVENT_FIELDS
    assert gb.calendar_events({"view": "lean"})["items"][0]["status"] == "confirmed"


def test_lean_drops_response_envelope_metadata():
    result = gb.calendar_events({"view": "lean"})
    for noise in ("kind", "etag", "updated", "timeZone", "accessRole",
                  "defaultReminders", "description"):
        assert noise not in result


def test_lean_keeps_pagination_token():
    """Dropping this would silently truncate a multi-page calendar."""
    assert gb.calendar_events({"view": "lean"})["nextPageToken"] == "PAGE2"


def test_lean_omits_absent_keys_rather_than_nulling(monkeypatch):
    patch_everywhere(monkeypatch, gb, "google_json",
                        lambda *a, **k: {"items": [{"id": "x", "summary": "y"}]})
    assert gb.calendar_events({"view": "lean"})["items"] == [{"id": "x", "summary": "y"}]


def test_lean_is_substantially_smaller():
    import json
    full = len(json.dumps(gb.calendar_events({})))
    lean = len(json.dumps(gb.calendar_events({"view": "lean"})))
    assert lean < full * 0.5, f"expected >50% reduction, got {100 * (1 - lean / full):.1f}%"
