#!/usr/bin/env python3
import base64
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from bridge_base import BridgeError, BridgeHandler, serve


CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
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
        raise BridgeError(exc.code, {"google_error": detail})


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
        raise BridgeError(exc.code, {"google_error": detail})


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


def grocer_item_candidates(text):
    compact = compact_email_text(text)
    lines = [line.strip(" -•\t") for line in compact.splitlines()]
    candidates = []
    price = re.compile(r"^(€|§\s*€|-€|Was €)")
    qty = re.compile(r"^\d+$")
    header_or_noise = re.compile(r"(?i)^(qty|product|unit\s*price|total|saved|fridge|frozen|cupboard|bakery|delivery|unavailable|substitutions?|substituted with:|update your .*)$")
    for i, line in enumerate(lines[:-1]):
        if not qty.match(line):
            continue
        product = re.sub(r"^[†§]\s*", "", lines[i + 1].strip())
        if len(product) < 4 or len(product) > 140:
            continue
        if price.search(product) or qty.match(product) or header_or_noise.match(product):
            continue
        candidates.append(product)

    skip = re.compile(r"(?i)(clubcard|subtotal|total|delivery|receipt|order|payment|unavailable|substitution|privacy|terms|vat|help|customer|barcode|quantity|price|eur|€)")
    productish = re.compile(r"(?i)(\b\d+\s?(g|kg|ml|l|pack|pk|pcs|slices|ct)\b|\b(fresh|organic|finest|free range|whole|semi skimmed|chicken|beef|pork|salmon|cod|egg|milk|cheese|yoghurt|bread|rice|pasta|potato|tomato|onion|pepper|apple|banana|lettuce|carrot|broccoli|beans|sauce|soup|cereal)\b)")
    for line in lines:
        if len(line) < 4 or len(line) > 140:
            continue
        if skip.search(line):
            continue
        if productish.search(line):
            candidates.append(line)
    deduped = []
    seen = set()
    for item in candidates:
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped[:120]


def line_candidates(text, limit=160):
    compact = compact_email_text(text)
    lines = [line.strip(" -•\t") for line in compact.splitlines()]
    result = []
    seen = set()
    boilerplate = re.compile(r"(?i)(unsubscribe|privacy policy|terms|manage preferences|view in browser|copyright|all rights reserved|do not reply)")
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
    max_results = min(int(body.get("max_results", 10)), 25)
    params = urllib.parse.urlencode({"q": query, "maxResults": max_results})
    return google_json("GET", f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{params}") or {}


def gmail_read(body):
    message_id = str(body.get("message_id", "")).strip()
    if not message_id:
        raise BridgeError(400, "message_id is required")
    fmt = urllib.parse.quote(str(body.get("format", "full")))
    message = google_json("GET", f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{urllib.parse.quote(message_id)}?format={fmt}") or {}
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
    payload = {
        "name": name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }
    return google_json("POST", "https://gmail.googleapis.com/gmail/v1/users/me/labels", payload) or {}


def gmail_list_labels(_body):
    return google_json("GET", "https://gmail.googleapis.com/gmail/v1/users/me/labels") or {}


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
        payload = {"addLabelIds": [str(x) for x in label_ids]}
    else:
        raise BridgeError(400, "allowed actions: mark_read, archive, add_labels")
    return google_json("POST", f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{urllib.parse.quote(message_id)}/modify", payload) or {}


def search_grocer_orders(body):
    query = str(body.get("query", "")).strip() or '(from:(mail.grocer.com OR grocer.com OR grocer.ie) (receipt OR order OR delivery)) newer_than:60d'
    max_results = min(int(body.get("max_results", 10)), 10)
    found = gmail_search({"query": query, "max_results": max_results}).get("messages", []) or []
    messages = []
    for item in found:
        try:
            messages.append(gmail_read({"message_id": item["id"]}))
        except Exception as exc:
            messages.append({"id": item.get("id"), "error": str(exc)})
    return {"query": query, "messages": messages}


def extract_grocer_order(body):
    query = str(body.get("query", "")).strip() or '(from:(mail.grocer.com OR grocer.com OR grocer.ie) (receipt OR order OR delivery)) newer_than:60d'
    max_results = min(int(body.get("max_results", 3)), 5)
    found = gmail_search({"query": query, "max_results": max_results}).get("messages", []) or []
    orders = []
    for item in found:
        message = gmail_read({"message_id": item["id"]})
        text = message.get("text", "")
        orders.append({
            "id": message.get("id"),
            "threadId": message.get("threadId"),
            "headers": message.get("headers", {}),
            "snippet": message.get("snippet", ""),
            "item_candidates": grocer_item_candidates(text),
            "text_excerpt": text[:4000],
        })
    return {"query": query, "orders": orders}


def calendar_list(_body):
    return google_json("GET", "https://www.googleapis.com/calendar/v3/users/me/calendarList") or {}


def calendar_events(body):
    calendar_id = urllib.parse.quote(str(body.get("calendar_id", "primary")), safe="")
    params = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": min(int(body.get("max_results", 25)), 100),
    }
    for key in ("timeMin", "timeMax", "q"):
        if body.get(key):
            params[key] = str(body[key])
    return google_json("GET", f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events?{urllib.parse.urlencode(params)}") or {}


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
    return google_json("POST", f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(calendar_id, safe='')}/events", event) or {}


SCHEMA = {"service": "google-workspace-bridge", "tools": [
    "POST /v1/gmail/search", "POST /v1/gmail/read", "POST /v1/gmail/clean",
    "POST /v1/gmail/search_grocer_orders", "POST /v1/gmail/extract_grocer_order",
    "POST /v1/gmail/labels/list", "POST /v1/gmail/labels/create", "POST /v1/gmail/modify",
    "POST /v1/calendar/list", "POST /v1/calendar/events", "POST /v1/calendar/freebusy",
    "POST /v1/calendar/events/create",
]}

# Each route function takes the request body and returns a JSON-able payload.
_POST_ROUTES = {
    "/v1/gmail/search": gmail_search,
    "/v1/gmail/read": gmail_read,
    "/v1/gmail/clean": gmail_clean,
    "/v1/gmail/search_grocer_orders": search_grocer_orders,
    "/v1/gmail/extract_grocer_order": extract_grocer_order,
    "/v1/gmail/labels/list": gmail_list_labels,
    "/v1/gmail/labels/create": gmail_create_label,
    "/v1/gmail/modify": gmail_modify,
    "/v1/calendar/list": calendar_list,
    "/v1/calendar/events": calendar_events,
    "/v1/calendar/freebusy": calendar_freebusy,
    "/v1/calendar/events/create": calendar_create_event,
}


def _wrap(fn):
    return lambda handler, body: (200, fn(body or {}))


class GoogleWorkspaceBridge(BridgeHandler):
    server_version = "google-workspace-bridge/1.0"
    bridge_token = BRIDGE_TOKEN

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
        **{("POST", path): _wrap(fn) for path, fn in _POST_ROUTES.items()},
    }


if __name__ == "__main__":
    serve(GoogleWorkspaceBridge)
