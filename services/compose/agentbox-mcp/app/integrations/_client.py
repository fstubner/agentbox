"""The HTTP client every integration uses to call its bridge.

- **Identity travels with each call.** The server resolves the caller's
  identity during auth and sets it here in a context variable, and every
  bridge request carries it as `X-Agentbox-Identity`. A bridge trusts the
  header because only agentbox-mcp holds its token. The header states who the
  server is acting for, and the model cannot set it.

- **Per-person routing.** `GOOGLE_BRIDGE_URL_<NAME>` and `_TOKEN_<NAME>`
  override the shared `GOOGLE_BRIDGE_URL` and `_TOKEN` for that person. Each
  person's Google bridge is a separate container holding only their token.
  The routing table selects which bridge a session reaches, and the model
  cannot change it. Without an override, calls go to the shared bridge, which
  is right for shared services like tasks.

Identity is a context variable instead of a parameter, so the integrations'
dispatch functions need no identity argument. Each request runs in its own thread, so
concurrent requests cannot see each other's identity.
"""
from __future__ import annotations

import contextvars
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from mcp_base import ToolError

CURRENT_IDENTITY: contextvars.ContextVar[str] = contextvars.ContextVar(
    "agentbox_identity", default="")


def bridge_client(env_prefix: str, default_host: str, timeout: int = 20):
    """Build a bridge_request(method, path, payload=None, query=None)."""
    default_url = f"http://{default_host}:8080"

    def bridge_request(method, path, payload=None, query=None):
        identity = CURRENT_IDENTITY.get()
        url_base = token = None
        if identity:
            suffix = identity.upper().replace("-", "_")
            url_base = os.environ.get(f"{env_prefix}_BRIDGE_URL_{suffix}")
            token = os.environ.get(f"{env_prefix}_BRIDGE_TOKEN_{suffix}")
        url_base = (url_base or os.environ.get(f"{env_prefix}_BRIDGE_URL",
                                               default_url)).rstrip("/")
        token = token or os.environ.get(f"{env_prefix}_BRIDGE_TOKEN", "")
        if not token:
            raise ToolError(f"{env_prefix}_BRIDGE_TOKEN is not configured")

        target = url_base + path
        if query:
            clean = {k: v for k, v in query.items() if v not in (None, "")}
            if clean:
                target += ("&" if "?" in target else "?") + urllib.parse.urlencode(clean)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if identity:
            headers["X-Agentbox-Identity"] = identity
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(target, data=data, method=method,
                                         headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            try:
                detail = json.loads(detail).get("error", detail)
            except ValueError:
                pass
            raise ToolError(f"bridge HTTP {exc.code}: {detail}") from None
        except urllib.error.URLError as exc:
            raise ToolError(f"bridge connection failed: {exc.reason}") from None

    return bridge_request
