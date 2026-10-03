#!/usr/bin/env python3
"""agentbox-mcp, the assistant's one tool server, in front of every bridge.

Every tool comes from an integration module, and every call goes through one
policy check and one dispatch point.

## Identity

The server serves one or more people. Which person is calling is decided by
the bearer token presented, in `mcp_base._require_auth`, before any tool runs.
It is never taken from a tool argument. If the assistant could choose who to
act as, an instruction in an email could choose too, and one compromised
conversation could reach everyone's accounts.

The identity then does three things.

- It picks the bridge a call goes to (`GOOGLE_BRIDGE_URL_<NAME>`), so each
  person's mail credential lives in its own container.
- It travels to the bridge in `X-Agentbox-Identity`, so grants can be scoped
  to one person.
- It is written to the outcome journal, so reflection knows whose calls it is
  reading.

## Credentials

This server holds bridge tokens and never an upstream credential. A leak here,
such as a log line or an error that echoes the environment, exposes a local,
revocable bridge token rather than access to somebody's mail. The bridges
publish no host ports, so a leaked token is only usable from inside this
container.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from integrations import (
    _client,
    builder,
    google,
    homeassistant,
    memory,
    portal,
    rulebook,
    speaker,
    vikunja,
)
from mcp_base import McpHandler, ToolError, serve

INTEGRATIONS = {
    "vikunja": vikunja,
    "memory": memory,
    "google": google,
    "builder": builder,
    "homeassistant": homeassistant,
    "speaker": speaker,
    "rulebook": rulebook,
    # harness is retired along with the router it dispatched to, and is not
    # wired in. See router/README.md.
    "portal": portal,
}


def _assemble() -> tuple[list[dict], dict[str, str]]:
    """Merge every integration's tools, refusing duplicate names.

    With two tools of the same name, dispatch would depend on dict order and
    could route a call to the wrong bridge. Failing at startup makes it one
    obvious error.
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
    """Parse AGENTBOX_IDENTITIES, which looks like `alex:token,sam:token`.

    Empty means single-operator mode, with one shared token and no identity.
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

    The caller's identity is published in a context variable rather than passed
    down, so the integrations' dispatch functions do not each need a parameter
    for it.
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
    # Empty because readiness is checked per bridge below, not through one
    # upstream URL.
    bridge_url = ""

    def _handle(self, message):  # type: ignore[override]
        # Publish the identity from _require_auth so the bridge client uses it
        # for this request. Each request runs in its own thread with its own
        # context, so concurrent requests cannot see each other's identity.
        _client.CURRENT_IDENTITY.set(self.identity or "")
        return super()._handle(message)

    def do_GET(self) -> None:  # type: ignore[override]
        if self.path == "/ready":
            return self._ready()
        if self.path == "/health":
            # The tool count lets `doctor` tell whether the gateway config is
            # hiding tools from the assistant.
            return self.send_json(200, {"ok": True, "service": self.service_name,
                                        "tools": len(self.tools),
                                        "integrations": len(INTEGRATIONS)})
        return super().do_GET()

    def _ready(self) -> None:
        """Ready when every configured bridge is ready.

        Each bridge is probed, and any one that is not ready makes this not
        ready, with the bridge named. Otherwise `doctor` could show green while
        some of the assistant's tools were dead.
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
    # The rules evaluator runs in this process because firing a rule is a tool
    # call, with the same dispatch, policy check, identity and outcome journal.
    # A separate service would need its own copy of all four.
    import evaluator
    evaluator.start(dispatch)
    serve(AgentboxMcp)
