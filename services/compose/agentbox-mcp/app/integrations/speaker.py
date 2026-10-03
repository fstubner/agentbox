"""Speaking aloud through the household's Bluetooth speakers.

The speakers are attached to the host, not a container, so this reaches
cli/agentbox-speaker through host.docker.internal, as the Home Assistant bridge
reaches Home Assistant.

Quiet hours live in that service, not in approval-policy.yaml. A policy tier
can be granted. A limit in the process holding the speaker cannot be granted
or bypassed, which matters for a device that can wake people up.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from mcp_base import ToolError, schema_object

SPEAKER_URL = os.environ.get("AGENTBOX_SPEAKER_URL",
                             "http://host.docker.internal:8772")
SPEAKER_TOKEN = os.environ.get("AGENTBOX_SPEAKER_TOKEN", "")

TOOLS = [
    {"name": "speak_aloud",
     "description": "Say something out loud on a household speaker. Use your "
                    "own words. This is for telling someone in the room "
                    "something, not for reading out a message or document you "
                    "received. Refused during quiet hours, and not queued for "
                    "later.",
     "inputSchema": schema_object({
         "text": {"type": "string",
                  "description": "What to say, in your own words. Short, because "
                                 "somebody is standing there listening."},
         "speaker": {"type": "string",
                     "description": "Which speaker, by name. Omit for the "
                                    "default. Use list_speakers to see them."}},
         ["text"])},

    {"name": "list_speakers",
     "description": "Which speakers exist, and whether it is currently quiet "
                    "hours.",
     "inputSchema": schema_object({})},
]

ROUTES = {"speak_aloud": "/v1/speak", "list_speakers": "/v1/speakers"}


def dispatch(name, args):
    path = ROUTES.get(name)
    if path is None:
        raise ToolError(f"unknown tool: {name}")
    request = urllib.request.Request(
        SPEAKER_URL + path, data=json.dumps(args or {}).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {SPEAKER_TOKEN}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        # A refusal is returned as a result, not raised as an error. Quiet
        # hours come back as 403, and the assistant should read the reason
        # and not retry.
        try:
            return json.loads(detail)
        except ValueError:
            raise ToolError(f"speaker service returned {exc.code}: "
                            f"{detail}") from None
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"speaker service unreachable: "
                        f"{type(exc).__name__}") from None
