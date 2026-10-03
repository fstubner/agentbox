"""Gmail routes: search, read, labels within the assistant's namespace, and drafts."""
from __future__ import annotations

import base64
import html
import re
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

from bridge_base import BridgeError, resolve_limit
from google_api import GMAIL_API, OWNED_LABEL_PREFIX, clean_text, decode_b64url, google_json


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
        "snippet": clean_text(message.get("snippet", "")),
        "headers": {
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "date": headers.get("date", ""),
            "subject": headers.get("subject", ""),
        },
        "text": clean_text(text[:50000]),
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
        "clean_text": clean_text(text[:12000]),
        "line_candidates": line_candidates(text),
    }


def gmail_create_label(body):
    name = str(body.get("name", "")).strip()
    if not name:
        raise BridgeError(400, "name is required")
    if not name.startswith(OWNED_LABEL_PREFIX):
        name = f"{OWNED_LABEL_PREFIX}{name}"
    # A label is identified by its name, so creating one that exists returns
    # it. A retried call leaves the account the same as a single call.
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

    Creating labels is restricted to the OWNED_LABEL_PREFIX namespace, and this
    applies the same rule to attaching them, so the assistant cannot attach a
    label the owner's own filters act on. Label ids can come from a model that
    has just read untrusted email.
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
    """Compose a draft. The only write that leads towards sending.

    Sending cannot be undone and is never exposed. A draft leaves that step to
    a person who can read it in Gmail first, and needs no approval because
    nothing leaves the account.
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
