#!/usr/bin/env python3
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from bridge_base import BridgeError, BridgeHandler, serve  # noqa: F401  BridgeError is re-exported
from google_api import (  # noqa: F401
    BRIDGE_TOKEN,
    DRIVE_MAX_TEXT,
    access_token,
)
from google_calendar import (  # noqa: F401
    LEAN_EVENT_FIELDS,
    calendar_create_event,
    calendar_events,
    calendar_freebusy,
    calendar_list,
)
from google_drive import (  # noqa: F401
    _PERSON_CACHE,
    PEOPLE_BATCH_MAX,
    _activity_actor,
    _actor_person_name,
    _drive_folder_guard,
    drive_activity,
    drive_create,
    drive_list_folder,
    drive_read,
    drive_recent,
    drive_search,
    drive_sharing,
    resolve_people,
)
from google_gmail import (  # noqa: F401
    gmail_clean,
    gmail_create_draft,
    gmail_create_label,
    gmail_list_labels,
    gmail_modify,
    gmail_read,
    gmail_search,
)

HOST = os.environ.get("BRIDGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("BRIDGE_PORT", "8080"))
MAX_BODY_BYTES = 128 * 1024


SCHEMA = {"service": "google-workspace-bridge", "tools": [
    "POST /v1/gmail/search", "POST /v1/gmail/read", "POST /v1/gmail/clean",
    "POST /v1/gmail/drafts/create",
    "POST /v1/gmail/labels/list", "POST /v1/gmail/labels/create", "POST /v1/gmail/modify",
    "POST /v1/calendar/list", "POST /v1/calendar/events", "POST /v1/calendar/freebusy",
    "POST /v1/calendar/events/create",
]}


_POST_ROUTES = {
    "/v1/drive/search": drive_search,
    "/v1/drive/read": drive_read,
    "/v1/drive/list": drive_list_folder,
    "/v1/drive/create": drive_create,
    "/v1/drive/activity": drive_activity,
    "/v1/drive/sharing": drive_sharing,
    "/v1/drive/recent": drive_recent,
    "/v1/gmail/search": gmail_search,
    "/v1/gmail/read": gmail_read,
    "/v1/gmail/clean": gmail_clean,
    "/v1/gmail/drafts/create": gmail_create_draft,
    "/v1/gmail/labels/list": gmail_list_labels,
    "/v1/gmail/labels/create": gmail_create_label,
    "/v1/gmail/modify": gmail_modify,
    "/v1/calendar/list": calendar_list,
    "/v1/calendar/events": calendar_events,
    "/v1/calendar/freebusy": calendar_freebusy,
    "/v1/calendar/events/create": calendar_create_event,
}


# Routes that need the handler, to record the resolved view in the log line.
# POST bodies are never logged, so otherwise a lean calendar call would look the
# same as a full one in the traffic record.
_HANDLER_AWARE = frozenset({"/v1/calendar/events"})


def _wrap(fn, path):
    if path in _HANDLER_AWARE:
        return lambda handler, body: (200, fn(body or {}, handler))
    return lambda handler, body: (200, fn(body or {}))


class GoogleWorkspaceBridge(BridgeHandler):
    server_version = "google-workspace-bridge/1.0"
    bridge_token = BRIDGE_TOKEN

    # agentbox-mcp checks first for a quick refusal with a clear message. This
    # is the check that counts, in the process that holds the OAuth token.
    def capability_for(self, method, path, body):
        if path == "/v1/drive/create":
            return "drive_write_own_folder"
        if path in ("/v1/drive/search", "/v1/drive/read", "/v1/drive/list",
                    "/v1/drive/activity", "/v1/drive/sharing",
                    "/v1/drive/recent"):
            return "drive_read"
        if path == "/v1/gmail/modify":
            action = str((body or {}).get("action", ""))
            if action in ("archive", "mark_read"):
                return "email_state_change"
            if action == "add_labels":
                return "email_label_own_namespace"
        return None

    def upstream_status(self):
        """Check the OAuth refresh token, which is this bridge's upstream.

        A revoked token is the most likely failure here. Without this check,
        every Gmail and Calendar route returns 500 while /health and /ready
        both report fine.
        """
        try:
            access_token()
            return {"ok": True, "upstream": {"google_oauth": "token refresh succeeded"}}
        except urllib.error.HTTPError as exc:
            detail = "unknown"
            try:
                body = json.loads(exc.read().decode("utf-8"))
                detail = body.get("error", "unknown")
            except Exception:  # noqa: BLE001 — diagnostics only
                pass
            hint = (" — refresh token revoked or expired; re-run the OAuth consent flow"
                    if detail == "invalid_grant" else "")
            return {"ok": False, "upstream": {"google_oauth": f"HTTP {exc.code}: {detail}{hint}"}}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "upstream": {"google_oauth": f"{type(exc).__name__}"}}
    routes = {
        ("GET", "/schema"): lambda handler, body: (200, SCHEMA),
        **{("POST", path): _wrap(fn, path) for path, fn in _POST_ROUTES.items()},
    }


if __name__ == "__main__":
    serve(GoogleWorkspaceBridge)
