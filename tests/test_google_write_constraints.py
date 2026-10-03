"""Tests for the Google bridge's write constraints.

- Applying a Gmail label must stay inside the agentbox/ namespace, the same
  rule as creating one.
- Creating a calendar event must not accept attendees, or Google would email
  invitations to anyone listed, turning "create event" into a way to send mail.

Both matter because label ids and event bodies can come from a model that has
just read untrusted email.
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
spec = importlib.util.spec_from_file_location("google_bridge_writes", BRIDGE)
gb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gb)


LABELS = {"labels": [
    {"id": "Label_1", "name": "agentbox/triage"},
    {"id": "Label_2", "name": "Important"},
    {"id": "Label_3", "name": "agentbox/receipts"},
]}


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    patch_everywhere(monkeypatch, gb, "gmail_list_labels", lambda _body: LABELS)
    patch_everywhere(monkeypatch, gb, "google_json", lambda *a, **k: {"ok": True})
    patch_everywhere(monkeypatch, gb, "ALLOWED_WRITE_CALENDAR_ID", "agent@group.calendar.google.com")
    patch_everywhere(monkeypatch, gb, "OWNED_LABEL_PREFIX", "agentbox/")


def test_owned_labels_may_be_applied():
    assert gb.gmail_modify({"message_id": "m1", "action": "add_labels",
                            "label_ids": ["Label_1", "Label_3"]}) == {"ok": True}


def test_foreign_label_is_refused():
    """'Important' was not created by the assistant, so it may not be applied."""
    with pytest.raises(gb.BridgeError) as exc:
        gb.gmail_modify({"message_id": "m1", "action": "add_labels",
                         "label_ids": ["Label_2"]})
    assert exc.value.status == 403


def test_mixing_one_foreign_label_refuses_the_whole_call():
    with pytest.raises(gb.BridgeError) as exc:
        gb.gmail_modify({"message_id": "m1", "action": "add_labels",
                         "label_ids": ["Label_1", "Label_2"]})
    assert exc.value.status == 403


def test_unknown_label_id_is_refused():
    """An id that matches no label cannot be proven owned, so it is refused."""
    with pytest.raises(gb.BridgeError) as exc:
        gb.gmail_modify({"message_id": "m1", "action": "add_labels",
                         "label_ids": ["Label_999"]})
    assert exc.value.status == 403


def test_event_without_attendees_is_allowed():
    assert gb.calendar_create_event({"event": {"summary": "focus block"}}) == {"ok": True}


def test_event_with_attendees_is_refused():
    """Google would email every attendee, which is send_external_communications."""
    with pytest.raises(gb.BridgeError) as exc:
        gb.calendar_create_event({"event": {"summary": "sync",
                                            "attendees": [{"email": "someone@example.com"}]}})
    assert exc.value.status == 403


def test_event_with_conference_data_is_refused():
    with pytest.raises(gb.BridgeError) as exc:
        gb.calendar_create_event({"event": {"summary": "sync",
                                            "conferenceData": {"createRequest": {}}}})
    assert exc.value.status == 403


def test_empty_attendees_list_is_not_treated_as_an_attempt():
    assert gb.calendar_create_event({"event": {"summary": "x", "attendees": []}}) == {"ok": True}


def test_writes_to_other_calendars_still_refused():
    with pytest.raises(gb.BridgeError) as exc:
        gb.calendar_create_event({"calendar_id": "personal@example.com",
                                  "event": {"summary": "x"}})
    assert exc.value.status == 403
