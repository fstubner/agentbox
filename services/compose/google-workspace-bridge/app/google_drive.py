"""Drive routes: search and read, activity and sharing as read-only views, and
writes confined to the assistant's folder."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from bridge_base import BridgeError, resolve_limit
from google_api import (
    AGENT_DRIVE_FOLDER_ID,
    DRIVE_API,
    DRIVE_EXPORT_AS,
    DRIVE_MAX_TEXT,
    clean_text,
    google_bytes,
    google_json,
)

# Each route function takes the request body and returns a JSON-able payload.
# --- Drive ---------------------------------------------------------------------
#
# Two scopes, and the difference between them is the security story.
#
#   drive.file      write access only to files this app created. Google
#                   enforces it, so even a compromised bridge cannot touch
#                   other files.
#   drive.readonly  read access to the whole drive. Only requested when the
#                   operator opts in, because it means the assistant can read
#                   everything stored there.
#
# Without GOOGLE_AGENT_DRIVE_FOLDER_ID there are no Drive writes at all.


def _drive_folder_guard(parents):
    """Refuse any write outside the agent's folder.

    drive.file already stops this bridge touching anyone else's files. This
    also stops it scattering its own across the drive.
    """
    if not AGENT_DRIVE_FOLDER_ID:
        raise BridgeError(503, "GOOGLE_AGENT_DRIVE_FOLDER_ID is not configured, "
                               "so Drive writes are unavailable")
    for parent in parents or []:
        if parent != AGENT_DRIVE_FOLDER_ID:
            raise BridgeError(403, f"files may only be created in the agent "
                                   f"folder {AGENT_DRIVE_FOLDER_ID}")
    return [AGENT_DRIVE_FOLDER_ID]


DRIVE_FIELDS = "id,name,mimeType,modifiedTime,size,owners(displayName),webViewLink"
ACTIVITY_API = "https://driveactivity.googleapis.com/v2"
# The activity feed is deeply nested. Flattening it here keeps that shape out
# of the model's context, for the same reason the calendar has a `lean` view.
ACTIVITY_ACTIONS = ("create", "edit", "move", "rename", "delete", "restore",
                    "permissionChange", "comment", "dlpChange", "reference",
                    "settingsChange")
PEOPLE_API = "https://people.googleapis.com/v1"
# Display names barely change, so they are cached for the life of the
# process. A deploy restarts the container, which picks up a renamed contact.
PERSON_CACHE_TTL = int(os.environ.get("GOOGLE_PERSON_CACHE_TTL", "3600"))
PEOPLE_BATCH_MAX = 200          # the API's documented ceiling
_PERSON_CACHE: dict[str, tuple[str, float]] = {}


def drive_search(body):
    """Search Drive by name and full text.

    The query is built here rather than accepted from the caller. A raw query
    string would let a caller ask for things the tool schema never offered.
    """
    text = str(body.get("query", "")).strip()
    if not text:
        raise BridgeError(400, "query is required")
    # Escape the quote so the caller's text cannot end the literal and be read
    # as query syntax.
    safe = text.replace("\\", "\\\\").replace("'", "\\'")
    clauses = [f"(name contains '{safe}' or fullText contains '{safe}')",
               "trashed = false"]
    if body.get("folder_id"):
        folder = str(body["folder_id"]).replace("'", "")
        clauses.append(f"'{folder}' in parents")
    if body.get("mime_type"):
        mime = str(body["mime_type"]).replace("'", "")
        clauses.append(f"mimeType = '{mime}'")
    params = urllib.parse.urlencode({
        "q": " and ".join(clauses),
        "pageSize": resolve_limit(body.get("max_results"), 10, 50),
        "fields": f"files({DRIVE_FIELDS})",
        "orderBy": "modifiedTime desc",
    })
    return google_json("GET", f"{DRIVE_API}/files?{params}") or {}


def drive_metadata(file_id):
    params = urllib.parse.urlencode({"fields": DRIVE_FIELDS})
    return google_json("GET", f"{DRIVE_API}/files/{urllib.parse.quote(file_id)}"
                              f"?{params}") or {}


def drive_read(body):
    """Read one file as text.

    The text returned is untrusted input, like an email body. A document can
    contain instructions, and a shared one can be written by someone else.
    Nothing here interprets it, and the field name says what it is.
    """
    file_id = str(body.get("file_id", "")).strip()
    if not file_id:
        raise BridgeError(400, "file_id is required")
    meta = drive_metadata(file_id)
    mime = str(meta.get("mimeType", ""))
    if mime == "application/vnd.google-apps.folder":
        raise BridgeError(400, "that is a folder, not a file")
    quoted = urllib.parse.quote(file_id)
    if mime in DRIVE_EXPORT_AS:
        params = urllib.parse.urlencode({"mimeType": DRIVE_EXPORT_AS[mime]})
        url = f"{DRIVE_API}/files/{quoted}/export?{params}"
    else:
        url = f"{DRIVE_API}/files/{quoted}?alt=media"
    raw = google_bytes("GET", url)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise BridgeError(415, f"{mime or 'that file'} is not text and cannot "
                               f"be read as text") from None
    truncated = len(text) > DRIVE_MAX_TEXT
    return {
        "id": meta.get("id"),
        "name": meta.get("name"),
        "mimeType": mime,
        "modifiedTime": meta.get("modifiedTime"),
        "webViewLink": meta.get("webViewLink"),
        "untrusted_text": clean_text(text[:DRIVE_MAX_TEXT]),
        "truncated": truncated,
    }


def drive_list_folder(body):
    folder = str(body.get("folder_id") or AGENT_DRIVE_FOLDER_ID).strip()
    if not folder:
        raise BridgeError(400, "folder_id is required")
    params = urllib.parse.urlencode({
        "q": f"'{folder.replace(chr(39), '')}' in parents and trashed = false",
        "pageSize": resolve_limit(body.get("max_results"), 25, 100),
        "fields": f"files({DRIVE_FIELDS})",
        "orderBy": "modifiedTime desc",
    })
    return google_json("GET", f"{DRIVE_API}/files?{params}") or {}


def resolve_people(resource_names):
    """Map people/{account_id} to a display name, in one batched request.

    Returns only what it could resolve, and leaves out anyone it could not
    rather than guess. A failure degrades to no names rather than an error,
    because names are a courtesy on top of the activity feed.
    """
    wanted = [n for n in dict.fromkeys(resource_names) if n]
    if not wanted:
        return {}
    resolved: dict[str, str] = {}
    stamp = time.time()
    misses = []
    for name in wanted:
        cached = _PERSON_CACHE.get(name)
        if cached and cached[1] > stamp:
            if cached[0]:
                resolved[name] = cached[0]
        else:
            misses.append(name)
    for start in range(0, len(misses), PEOPLE_BATCH_MAX):
        chunk = misses[start:start + PEOPLE_BATCH_MAX]
        query = [("personFields", "names,emailAddresses")]
        query += [("resourceNames", name) for name in chunk]
        try:
            raw = google_json(
                "GET", f"{PEOPLE_API}/people:batchGet?"
                       f"{urllib.parse.urlencode(query)}") or {}
        except BridgeError:
            # Most likely the contacts scope was never granted. Remember the
            # failure briefly so it does not cost a request on every query.
            for name in chunk:
                _PERSON_CACHE[name] = ("", stamp + 300)
            continue
        returned = {}
        for entry in raw.get("responses", []):
            person = entry.get("person") or {}
            key = entry.get("requestedResourceName") or person.get("resourceName")
            names = person.get("names") or []
            emails = person.get("emailAddresses") or []
            label = ""
            if names:
                label = str(names[0].get("displayName", "")).strip()
            if not label and emails:
                label = str(emails[0].get("value", "")).strip()
            if key and label:
                returned[key] = label
        for name in chunk:
            label = returned.get(name, "")
            _PERSON_CACHE[name] = (label, stamp + PERSON_CACHE_TTL)
            if label:
                resolved[name] = label
    return resolved


def _actor_person_name(actor):
    """The people/{id} this actor refers to, if any."""
    if not isinstance(actor, dict):
        return ""
    known = actor.get("user", {}).get("knownUser", {})
    if known.get("isCurrentUser"):
        return ""
    return str(known.get("personName", ""))


def _activity_actor(actor, names=None):
    """A display name for whoever acted, or "someone".

    Resolved through the People API where possible. If the person is not a
    contact or the scope is missing, the answer is "someone" rather than a
    made-up name.
    """
    if not isinstance(actor, dict):
        return "unknown"
    user = actor.get("user", {})
    known = user.get("knownUser", {})
    if known.get("isCurrentUser"):
        return "you"
    if "user" in actor:
        person = str(known.get("personName", ""))
        resolved = (names or {}).get(person, "")
        if resolved:
            return resolved
        if "deletedUser" in user:
            return "a deleted account"
        return "someone"
    for kind in ("impersonation", "system", "administrator", "anonymous"):
        if kind in actor:
            return kind
    return "unknown"


def _activity_kind(detail):
    for name in ACTIVITY_ACTIONS:
        if name in (detail or {}):
            return name
    return "unknown"


def drive_activity(body):
    """Who changed what, and when.

    drive.activity.readonly can see history but not change it, which answers
    "who edited the budget?" without granting anything that could do harm.
    """
    payload = {"pageSize": resolve_limit(body.get("max_results"), 20, 100)}
    file_id = str(body.get("file_id", "")).strip()
    folder_id = str(body.get("folder_id", "")).strip()
    if file_id and folder_id:
        raise BridgeError(400, "give file_id or folder_id, not both")
    if file_id:
        payload["itemName"] = f"items/{file_id}"
    elif folder_id:
        payload["ancestorName"] = f"items/{folder_id}"
    else:
        payload["ancestorName"] = "items/root"
    raw = google_json("POST", f"{ACTIVITY_API}/activity:query", payload) or {}
    activities = raw.get("activities", [])
    # One batched lookup for the whole page, since the same few people recur.
    names = resolve_people([_actor_person_name(actor)
                            for activity in activities
                            for actor in activity.get("actors", [])])
    events = []
    for activity in activities:
        targets = []
        for target in activity.get("targets", []):
            item = target.get("driveItem", {})
            if item.get("title"):
                targets.append(item["title"])
        events.append({
            "when": activity.get("timestamp")
                    or activity.get("timeRange", {}).get("endTime"),
            "action": _activity_kind(activity.get("primaryActionDetail")),
            "actors": sorted({_activity_actor(a, names)
                              for a in activity.get("actors", [])}),
            "items": targets,
        })
    return {"activity": events, "count": len(events)}


def drive_sharing(body):
    """Who can currently see one file.

    Reading sharing settings is not changing them. "Is anything of mine public?"
    is worth being able to ask, and making something public belongs to a
    person, so this only lists.
    """
    file_id = str(body.get("file_id", "")).strip()
    if not file_id:
        raise BridgeError(400, "file_id is required")
    params = urllib.parse.urlencode({
        "fields": "permissions(id,type,role,emailAddress,domain,"
                  "allowFileDiscovery)"})
    raw = google_json("GET", f"{DRIVE_API}/files/"
                             f"{urllib.parse.quote(file_id)}/permissions"
                             f"?{params}") or {}
    entries = []
    public = False
    for entry in raw.get("permissions", []):
        kind = entry.get("type")
        if kind in ("anyone", "domain"):
            public = True
        entries.append({
            "who": entry.get("emailAddress") or entry.get("domain") or kind,
            "type": kind,
            "role": entry.get("role"),
        })
    return {"permissions": entries, "shared_beyond_owner": len(entries) > 1,
            "reachable_by_anyone_or_whole_domain": public}


def drive_recent(body):
    """Recently modified files, without needing a search term."""
    params = urllib.parse.urlencode({
        "q": "trashed = false",
        "pageSize": resolve_limit(body.get("max_results"), 15, 50),
        "fields": f"files({DRIVE_FIELDS})",
        "orderBy": "modifiedTime desc",
    })
    return google_json("GET", f"{DRIVE_API}/files?{params}") or {}


def drive_create(body):
    """Create a plain-text or markdown file in the agent's folder.

    No sharing, no permission changes and no overwriting. A permission change
    is how a private document becomes a public link, and that belongs to a
    person who can see what they are publishing.
    """
    name = str(body.get("name", "")).strip()
    content = str(body.get("content", ""))
    if not name:
        raise BridgeError(400, "name is required")
    parents = _drive_folder_guard(body.get("parents"))
    mime = str(body.get("mime_type", "text/plain")).strip() or "text/plain"
    if mime not in ("text/plain", "text/markdown", "text/csv"):
        raise BridgeError(400, "mime_type must be text/plain, text/markdown "
                               "or text/csv")
    boundary = "agentbox-drive-boundary"
    metadata = json.dumps({"name": name, "parents": parents, "mimeType": mime})
    payload = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n{content}\r\n"
        f"--{boundary}--\r\n").encode()
    params = urllib.parse.urlencode({"uploadType": "multipart",
                                     "fields": DRIVE_FIELDS})
    return google_json(
        "POST", f"https://www.googleapis.com/upload/drive/v3/files?{params}",
        raw_body=payload,
        content_type=f"multipart/related; boundary={boundary}") or {}
