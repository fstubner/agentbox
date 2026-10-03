"""Calendar routes: lean event listings, free/busy, and events on the assistant's own calendar."""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

from bridge_base import BridgeError, project_fields, resolve_view
from google_api import ALLOWED_WRITE_CALENDAR_ID, CALENDAR_API, google_json


def calendar_list(_body):
    return google_json("GET", "https://www.googleapis.com/calendar/v3/users/me/calendarList") or {}


# Events are the largest payload in the system, about 7,000 tokens for ten.
# `status` is kept even though "what is on my calendar" does not need it,
# because without it a cancelled event looks like a live one.
LEAN_EVENT_FIELDS = ("id", "summary", "start", "end", "location", "status")

# Response-level metadata that costs tokens and answers nothing.
LEAN_ENVELOPE_FIELDS = ("summary", "nextPageToken")


def calendar_events(body, handler=None):
    view = resolve_view(body.get("view"))
    if handler is not None:
        handler.note("view", view)
    calendar_id = urllib.parse.quote(str(body.get("calendar_id", "primary")), safe="")
    params = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": min(int(body.get("max_results", 25)), 100),
    }
    for key in ("timeMin", "timeMax", "q"):
        if body.get(key):
            params[key] = str(body[key])
    result = google_json(
        "GET",
        f"{CALENDAR_API}/calendars/{calendar_id}"
        f"/events?{urllib.parse.urlencode(params)}") or {}
    if view != "lean" or not isinstance(result, dict):
        return result
    lean = {k: result[k] for k in LEAN_ENVELOPE_FIELDS if k in result}
    lean["items"] = project_fields(result.get("items", []), LEAN_EVENT_FIELDS)
    return lean


def calendar_freebusy(body):
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise BridgeError(400, "items must be a non-empty list of calendar ids")
    payload = {
        "timeMin": body.get("timeMin"),
        "timeMax": body.get("timeMax"),
        "items": [{"id": str(item)} for item in items],
    }
    if not payload["timeMin"] or not payload["timeMax"]:
        raise BridgeError(400, "timeMin and timeMax are required")
    return google_json("POST", "https://www.googleapis.com/calendar/v3/freeBusy", payload) or {}


def calendar_create_event(body):
    if not ALLOWED_WRITE_CALENDAR_ID:
        raise BridgeError(503, "GOOGLE_ALLOWED_WRITE_CALENDAR_ID is not configured")
    calendar_id = str(body.get("calendar_id", ALLOWED_WRITE_CALENDAR_ID)).strip()
    if calendar_id != ALLOWED_WRITE_CALENDAR_ID:
        raise BridgeError(403, "writes are only allowed to the configured agent calendar")
    event = body.get("event")
    if not isinstance(event, dict):
        raise BridgeError(400, "event object is required")
    # Google emails an invitation to everyone in `attendees`, so accepting
    # them would make creating an event a way to send email. Sending external
    # communication needs approval, so attendees are refused.
    for field in ("attendees", "conferenceData"):
        if event.get(field):
            raise BridgeError(403, f"'{field}' is not permitted: creating an event may not "
                                   f"notify other people. Ask the operator to invite attendees.")
    return google_json(
        "POST",
        f"{CALENDAR_API}/calendars/"
        f"{urllib.parse.quote(calendar_id, safe='')}"
        # sendUpdates=none is the constraint, not a default. It stops an
        # injected instruction turning an event into an email to anyone.
        f"/events?sendUpdates=none", event) or {}
