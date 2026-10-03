"""Putting something on a screen, with detail only on screens marked private."""
from __future__ import annotations

import os

from bridge_base import BridgeError
from ha_access import require_controllable
from ha_api import call_service

# --- screens ------------------------------------------------------------------
#
# Like a phone's lock screen, a shared screen gets the summary and a private one
# gets the detail too. `detail` is dropped before the request to Home Assistant
# is built, so a living-room TV cannot receive it even if the assistant sends
# it. The length cap stops the summary becoming a second detail field.
PRIVATE_SCREENS = frozenset(
    e.strip() for e in os.environ.get("HA_PRIVATE_SCREENS", "").split(",")
    if e.strip())
MAX_SUMMARY_CHARS = int(os.environ.get("HA_MAX_SUMMARY_CHARS", "80"))


def cast(handler, body):
    """Put something on a screen, at the detail level that screen allows.

    Shared screens get `summary`, and private screens also get `detail`. This
    is enforced here, not left to the assistant.
    """
    body = body or {}
    entity_id = str(body.get("entity_id") or "").strip()
    require_controllable(entity_id, ("media_player", "notify"))

    summary = str(body.get("summary") or "").strip()
    if not summary:
        raise BridgeError(400, "summary is required")
    if len(summary) > MAX_SUMMARY_CHARS:
        raise BridgeError(
            400, f"summary must be {MAX_SUMMARY_CHARS} characters or fewer, because it "
                 f"is the preview a shared screen shows, and a long one is a "
                 f"detail field wearing a disguise. Put the rest in 'detail'.")

    private = entity_id in PRIVATE_SCREENS
    detail = str(body.get("detail") or "").strip()
    message = f"{summary}\n\n{detail}" if (private and detail) else summary

    call_service("notify", "send_message",
                 {"entity_id": entity_id, "message": message})
    return 200, {
        "entity_id": entity_id,
        "screen": "private" if private else "shared",
        "showed_detail": bool(private and detail),
        "note": ("Full detail shown." if private else
                 "This screen is shared, so only the summary was shown. Detail "
                 "was discarded, not queued. Say it in conversation instead."),
    }
