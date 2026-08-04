#!/usr/bin/env python3
"""Agentbox MCP gateway — one front door, many bridges, many identities.

This replaces five near-identical MCP services. Each was a tool registry plus an
HTTP proxy, and each carried its own copy of the policy gate — policy replicated
five times is the opposite of a policy enforcement point.

The consolidation is not tidying. It is the topology `2026-07-28` was shaped
for: the header/body agreement we already implement exists so an intermediary
can route and filter without parsing bodies, the `-32020..-32099` range is a
gateway-class error allocation, and `server/discover` plus stateless
per-request versioning let one endpoint multiplex many backends without
per-connection handshake state. We had built gateway conformance into five
servers that were not a gateway.

## Identity

The gateway serves one or more identities. Which one is calling is decided by
**which bearer token was presented** — resolved in `mcp_base._require_auth`
before any tool runs, and never taken from a tool argument.

That distinction is the whole security property. If the assistant could name
the identity it wants to act as, an instruction embedded in an email could name
one too, and a single compromised context would reach both people's accounts.
Because identity is the credential, a process holding only one person's token
cannot act as anyone else — the other credential is simply not there to
present. Identity is bound to the session, not to a parameter.

Downstream, identity does three things:

- selects which bridge a call routes to (`GOOGLE_BRIDGE_URL_ALEX` and
  friends), so each person's mail credential lives in a separate container;
- travels to the bridge as `X-Agentbox-Identity` so the authoritative gate can
  match identity-scoped grants;
- is written to the outcome journal, so reflection and tier arguments can tell
  whose calls they are reading.

## Credential isolation, restated

The gateway holds bridge tokens and never an upstream credential. A leak here —
a log line, an error echoing the environment — costs a scoped, local, revocable
token, not a permanent handle on somebody's mail. That is why consolidating the
MCPs is safe while consolidating the *bridges* would not be.

One process now holds every bridge token instead of five processes holding one
each, which is a larger prize. Accepted deliberately: still no upstream
credentials, and one hardened front door is easier to reason about than five
copies drifting apart — which is exactly how the fail-open auth bug shipped.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp_base import McpHandler, ToolError, serve  # noqa: E402

from integrations import _client  # noqa: E402
from integrations import builder, google, homeassistant, memory, vikunja  # noqa: E402

INTEGRATIONS = {
    "vikunja": vikunja,
    "memory": memory,
    "google": google,
    "builder": builder,
    "homeassistant": homeassistant,
}


def _assemble() -> tuple[list[dict], dict[str, str]]:
    """Merge every integration's tools, refusing name collisions.

    Two integrations exposing the same tool name would make dispatch depend on
    dict ordering — silently routing a call to the wrong bridge. Refuse at
    startup instead, where it is one obvious error rather than an intermittent
    mystery.
    """
    tools: list[dict] = []
    owner: dict[str, str] = {}
    for name, module in INTEGRATIONS.items():
        for tool in getattr(module, "TOOLS", []):
            tool_name = tool.get("name", "")
            if tool_name in owner:
                raise SystemExit(
                    f"tool name collision: '{tool_name}' declared by both "
                    f"'{owner[tool_name]}' and '{name}'. Tool names are the "
                    f"assistant's whole namespace and must be unique.")
            owner[tool_name] = name
            tools.append(tool)
    return sorted(tools, key=lambda t: t["name"]), owner


TOOLS, TOOL_OWNER = _assemble()


def load_identities() -> dict[str, str]:
    """Parse AGENTBOX_IDENTITIES: `alex:token,sarah:token`.

    Empty means single-operator: fall back to one shared token with no
    identity, which is exactly how every existing deployment behaves.
    """
    raw = os.environ.get("AGENTBOX_IDENTITIES", "").strip()
    identities: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        name, _, token = entry.partition(":")
        name, token = name.strip(), token.strip()
        if name and token:
            identities[name] = token
    return identities


def dispatch(name: str, args: dict):
    """Route to the integration that declared this tool.

    The identity resolved at auth time is published here rather than passed
    down, because the integration modules' dispatch signatures predate identity
    and threading it through every call site would touch all of them to say the
    same thing.
    """
    owner = TOOL_OWNER.get(name)
    if owner is None:
        raise ToolError(f"unknown tool: {name}")
    return INTEGRATIONS[owner].dispatch(name, args)


class AgentboxMcp(McpHandler):
    service_name = "agentbox-mcp"
    instructions = (
        "One endpoint for tasks, memory, Google Workspace, the house, and this "
        "platform's own source. You cannot send mail, delete anything, unlock "
        "anything, merge your own code, or approve your own proposals.")
    tools = TOOLS
    dispatch = staticmethod(dispatch)
    identity_tokens = load_identities()
    shared_token = os.environ.get("AGENTBOX_MCP_SHARED_TOKEN", "")
    # Readiness is per-bridge below rather than the single upstream the base
    # class assumes, so this stays empty on purpose.
    bridge_url = ""

    def _handle(self, message):  # type: ignore[override]
        # Publish the identity resolved during _require_auth so the shared
        # bridge client picks it up for this request. Contextvars are
        # per-thread under ThreadingHTTPServer, so concurrent requests from
        # different identities cannot see each other's.
        _client.CURRENT_IDENTITY.set(self.identity or "")
        return super()._handle(message)

    def do_GET(self) -> None:  # type: ignore[override]
        if self.path == "/ready":
            return self._ready()
        if self.path == "/health":
            # Includes the tool count so `doctor` can tell whether the gateway
            # is narrowing what the assistant sees.
            return self.send_json(200, {"ok": True, "service": self.service_name,
                                        "tools": len(self.tools),
                                        "integrations": len(INTEGRATIONS)})
        return super().do_GET()

    def _ready(self) -> None:
        """Ready when every bridge that has a URL configured is ready.

        A gateway fronting five bridges cannot report a single upstream. Each
        is probed, and one unreachable bridge makes the gateway not-ready with
        the reason named — otherwise `doctor` would show green while a quarter
        of the assistant's tools were dead.
        """
        import urllib.error
        import urllib.request

        results: dict[str, Any] = {}
        ok = True
        for name, prefix, host in (
                ("vikunja", "VIKUNJA", "vikunja-bridge"),
                ("memory", "MEMORY", "memory-bridge"),
                ("google", "GOOGLE", "google-workspace-bridge"),
                ("builder", "BUILDER", "builder-bridge"),
                ("homeassistant", "HA", "homeassistant-bridge")):
            url = os.environ.get(f"{prefix}_BRIDGE_URL", f"http://{host}:8080")
            try:
                with urllib.request.urlopen(f"{url.rstrip('/')}/ready", timeout=10) as resp:
                    results[name] = json.loads(resp.read()).get("ok", True)
            except urllib.error.HTTPError as exc:
                results[name] = f"HTTP {exc.code}"
                ok = False
            except Exception as exc:  # noqa: BLE001
                results[name] = f"unreachable: {type(exc).__name__}"
                ok = False
        self.send_json(200 if ok else 503, {"ok": ok, "bridges": results})

if __name__ == "__main__":
    print(f"agentbox-mcp: {len(TOOLS)} tools from "
          f"{len(INTEGRATIONS)} integrations", file=sys.stderr, flush=True)
    serve(AgentboxMcp)
