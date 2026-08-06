#!/usr/bin/env python3
import base64
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

from bridge_base import BridgeError, BridgeHandler, project_fields, resolve_limit, resolve_view, serve

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")

# Named once so the call sites fit on a line and read as intent rather than URL.
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
BRIDGE_TOKEN = os.environ.get("GOOGLE_BRIDGE_TOKEN", "")
ALLOWED_WRITE_CALENDAR_ID = os.environ.get("GOOGLE_ALLOWED_WRITE_CALENDAR_ID", "")
OWNED_LABEL_PREFIX = os.environ.get("GOOGLE_OWNED_LABEL_PREFIX", "agentbox/")
HOST = os.environ.get("BRIDGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("BRIDGE_PORT", "8080"))
MAX_BODY_BYTES = 128 * 1024


def require_config():
    missing = [
        name for name, value in {
            "GOOGLE_CLIENT_ID": CLIENT_ID,
            "GOOGLE_CLIENT_SECRET": CLIENT_SECRET,
            "GOOGLE_REFRESH_TOKEN": REFRESH_TOKEN,
            "GOOGLE_BRIDGE_TOKEN": BRIDGE_TOKEN,
        }.items() if not value
    ]
    if missing:
        raise BridgeError(503, {"missing_env": missing})


def post_form(url, data):
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=encoded, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def access_token():
    require_config()
    token = post_form("https://oauth2.googleapis.com/token", {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token",
    })
    value = token.get("access_token")
    if not value:
        raise BridgeError(502, "Google token response did not include access_token")
    return value


def google_json(method, url, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {access_token()}")
    req.add_header("Accept", "application/json")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw[:500]
        raise BridgeError(exc.code, {"google_error": detail}) from None


def google_delete(url):
    req = urllib.request.Request(url, method="DELETE")
    req.add_header("Authorization", f"Bearer {access_token()}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
            return {"ok": True}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw[:500]
        raise BridgeError(exc.code, {"google_error": detail}) from None


def decode_b64url(value):
    if not value:
        return ""
    padded = value + "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")


def header_map(payload):
    headers = payload.get("headers", []) if isinstance(payload, dict) else []
    return {str(h.get("name", "")).lower(): h.get("value", "") for h in headers}


def extract_parts(payload):
    if not isinstance(payload, dict):
        return []
    parts = []
    body = payload.get("body", {})
    data = body.get("data") if isinstance(body, dict) else ""
    mime = payload.get("mimeType", "")
    if data:
        parts.append({"mimeType": mime, "text": decode_b64url(data)})
    for child in payload.get("parts", []) or []:
        parts.extend(extract_parts(child))
    return parts


def compact_email_text(text):
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|td|th|h[1-6])>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()



def line_candidates(text, limit=160):
    compact = compact_email_text(text)
    lines = [line.strip(" -•\t") for line in compact.splitlines()]
    result = []
    seen = set()
    boilerplate = re.compile(
        r"(?i)(unsubscribe|privacy policy|terms|manage preferences"
        r"|view in browser|copyright|all rights reserved|do not reply)")
    for line in lines:
        if len(line) < 4 or len(line) > 180:
            continue
        if boilerplate.search(line):
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(line)
        if len(result) >= limit:
            break
    return result


def gmail_search(body):
    query = str(body.get("query", "")).strip()
    if not query:
        raise BridgeError(400, "query is required")
    max_results = resolve_limit(body.get("limit", body.get("max_results")), default=10, maximum=25)
    params = urllib.parse.urlencode({"q": query, "maxResults": max_results})
    return google_json("GET", f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{params}") or {}


def gmail_read(body):
    message_id = str(body.get("message_id", "")).strip()
    if not message_id:
        raise BridgeError(400, "message_id is required")
    fmt = urllib.parse.quote(str(body.get("format", "full")))
    message = google_json(
        "GET",
        f"{GMAIL_API}/users/me/messages/"
        f"{urllib.parse.quote(message_id)}?format={fmt}") or {}
    payload = message.get("payload", {})
    headers = header_map(payload)
    parts = extract_parts(payload)
    raw_text = "\n\n".join(part["text"] for part in parts if part.get("text")).strip()
    text = compact_email_text(raw_text)
    return {
        "id": message.get("id"),
        "threadId": message.get("threadId"),
        "labelIds": message.get("labelIds", []),
        "snippet": message.get("snippet", ""),
        "headers": {
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "date": headers.get("date", ""),
            "subject": headers.get("subject", ""),
        },
        "text": text[:50000],
    }


def gmail_clean(body):
    message = gmail_read(body)
    text = message.get("text", "")
    return {
        "id": message.get("id"),
        "threadId": message.get("threadId"),
        "labelIds": message.get("labelIds", []),
        "snippet": message.get("snippet", ""),
        "headers": message.get("headers", {}),
        "clean_text": text[:12000],
        "line_candidates": line_candidates(text),
    }


def gmail_create_label(body):
    name = str(body.get("name", "")).strip()
    if not name:
        raise BridgeError(400, "name is required")
    if not name.startswith(OWNED_LABEL_PREFIX):
        name = f"{OWNED_LABEL_PREFIX}{name}"
    # Idempotent by nature: a label is identified by its name, so creating one
    # that exists returns it rather than failing or duplicating. A retried call
    # must not leave the account in a different state than a single call.
    for label in (gmail_list_labels({}) or {}).get("labels", []):
        if label.get("name") == name:
            return label
    payload = {
        "name": name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }
    return google_json("POST", "https://gmail.googleapis.com/gmail/v1/users/me/labels", payload) or {}


def gmail_list_labels(_body):
    return google_json("GET", "https://gmail.googleapis.com/gmail/v1/users/me/labels") or {}


def require_owned_labels(label_ids):
    """Only labels this assistant created may be applied to a message.

    create_gmail_label forces the OWNED_LABEL_PREFIX namespace, but applying
    labels took arbitrary IDs — so the assistant could attach any label in the
    account, including ones the operator's filters act on. Create was
    constrained and apply was not, which made the namespace decorative.

    This matters more than it looks: label IDs can arrive from a model that has
    just read untrusted email content, and a small worker model has been
    measured obeying instructions embedded in tool data.
    """
    labels = (gmail_list_labels({}) or {}).get("labels", [])
    owned = {label["id"] for label in labels
             if str(label.get("name", "")).startswith(OWNED_LABEL_PREFIX)}
    foreign = [lid for lid in label_ids if lid not in owned]
    if foreign:
        raise BridgeError(403, f"only {OWNED_LABEL_PREFIX}* labels may be applied; "
                               f"refused {len(foreign)} label(s) outside that namespace")


def gmail_modify(body):
    message_id = str(body.get("message_id", "")).strip()
    action = str(body.get("action", "")).strip()
    label_ids = body.get("label_ids", [])
    if not message_id:
        raise BridgeError(400, "message_id is required")
    if action == "mark_read":
        payload = {"removeLabelIds": ["UNREAD"]}
    elif action == "archive":
        payload = {"removeLabelIds": ["INBOX"]}
    elif action == "add_labels":
        if not isinstance(label_ids, list) or not label_ids:
            raise BridgeError(400, "label_ids must be a non-empty list")
        require_owned_labels([str(x) for x in label_ids])
        payload = {"addLabelIds": [str(x) for x in label_ids]}
    else:
        raise BridgeError(400, "allowed actions: mark_read, archive, add_labels")
    return google_json(
        "POST",
        f"{GMAIL_API}/users/me/messages/"
        f"{urllib.parse.quote(message_id)}/modify", payload) or {}




def gmail_create_draft(body):
    """Compose a draft. Deliberately the only write toward sending.

    Sending is irreversible and is never exposed; a draft leaves the
    irreversible step with a human who can read it in Gmail first. This is the
    same propose-then-approve shape as the memory review gate, and it needs no
    approval plumbing because nothing leaves the account.
    """
    to = body.get("to") or []
    if isinstance(to, str):
        to = [to]
    if not isinstance(to, list) or not all(isinstance(x, str) for x in to):
        raise BridgeError(400, "to must be a string or list of strings")
    subject = str(body.get("subject", "")).strip()
    text = str(body.get("body", ""))
    if not subject and not text:
        raise BridgeError(400, "subject or body is required")
    message = EmailMessage()
    if to:
        message["To"] = ", ".join(to)
    for header, value in (("Cc", body.get("cc")), ("Bcc", body.get("bcc"))):
        if value:
            message[header] = ", ".join(value) if isinstance(value, list) else str(value)
    message["Subject"] = subject
    message.set_content(text)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    payload = {"message": {"raw": raw}}
    if body.get("thread_id"):
        payload["message"]["threadId"] = str(body["thread_id"])
    created = google_json("POST", "https://gmail.googleapis.com/gmail/v1/users/me/drafts", payload) or {}
    return {"id": created.get("id"), "message": created.get("message", {}),
            "note": "draft created; it has NOT been sent"}


def calendar_list(_body):
    return google_json("GET", "https://www.googleapis.com/calendar/v3/users/me/calendarList") or {}


# The largest payload in the platform by a wide margin: 23 KB (~7k tokens) for
# ten events, 28x the full vikunja task list, and it lands in context on every
# schedule lookup. `status` is carried even though it is not needed to answer
# "what is on my calendar" — without it a cancelled event is indistinguishable
# from a live one, which is an accuracy loss, not a saving.
LEAN_EVENT_FIELDS = ("id", "summary", "start", "end", "location", "status")

# Response-level Google metadata that costs tokens and answers nothing:
# kind, etag, updated, timeZone, accessRole, defaultReminders, description.
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
    # The event body went to Google unvalidated. Google emails an invitation to
    # everyone in `attendees`, so a tool that looks purely local was an
    # unbounded outbound-communication channel — and the policy puts
    # send_external_communications behind approval. The calendar-id guard does
    # not contain this: the event lives on the agent calendar either way.
    for field in ("attendees", "conferenceData"):
        if event.get(field):
            raise BridgeError(403, f"'{field}' is not permitted: creating an event may not "
                                   f"notify other people. Ask the operator to invite attendees.")
    return google_json(
        "POST",
        f"{CALENDAR_API}/calendars/"
        f"{urllib.parse.quote(calendar_id, safe='')}"
        # sendUpdates=none is the constraint, not a default: it is what stops
        # an injected instruction turning an event into an email to anyone.
        f"/events?sendUpdates=none", event) or {}


SCHEMA = {"service": "google-workspace-bridge", "tools": [
    "POST /v1/gmail/search", "POST /v1/gmail/read", "POST /v1/gmail/clean",
    "POST /v1/gmail/drafts/create",
    "POST /v1/gmail/labels/list", "POST /v1/gmail/labels/create", "POST /v1/gmail/modify",
    "POST /v1/calendar/list", "POST /v1/calendar/events", "POST /v1/calendar/freebusy",
    "POST /v1/calendar/events/create",
]}

# Each route function takes the request body and returns a JSON-able payload.
_POST_ROUTES = {
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


# Routes that need the handler, to record the resolved view on the log line.
# POST bodies are never logged, so without this a lean calendar call would be
# indistinguishable from a full one in the traffic record.
_HANDLER_AWARE = frozenset({"/v1/calendar/events"})


def _wrap(fn, path):
    if path in _HANDLER_AWARE:
        return lambda handler, body: (200, fn(body or {}, handler))
    return lambda handler, body: (200, fn(body or {}))


class GoogleWorkspaceBridge(BridgeHandler):
    server_version = "google-workspace-bridge/1.0"
    bridge_token = BRIDGE_TOKEN

    # Gate and credential must not share a process. The MCP checks first for a
    # fast, informative denial; this is the authoritative check, in the process
    # that actually holds the OAuth token — so a compromised MCP cannot spend
    # what it does not have.
    def capability_for(self, method, path, body):
        if path == "/v1/gmail/modify":
            action = str((body or {}).get("action", ""))
            if action in ("archive", "mark_read"):
                return "email_state_change"
            if action == "add_labels":
                return "email_label_own_namespace"
        return None

    def upstream_status(self):
        """Validate the OAuth refresh token, which is this bridge's upstream.

        A credential bridge fronting a remote API looks like it has nothing to
        probe — it holds a secret rather than pointing at a service we run.
        That is wrong, and the mistake was expensive: the refresh token was
        revoked and every Gmail and Calendar route returned 500 while /health
        and a stubbed /ready both reported fine. An expired credential is the
        single most likely failure for this bridge, and it is checkable.
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
