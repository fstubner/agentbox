"""Example Agentbox bridge: an echo/status service.

Replace this file's routes with your service's calls. Keep `bridge_base.py`
unchanged. This example shows the whole contract in ~40 lines:

- one authenticated route (`POST /v1/echo`)
- reads its upstream credential from the environment (here just a demo value)
- never exposes that credential; it only acts on the caller's behalf
"""
from __future__ import annotations

import os
from typing import Any

from bridge_base import BridgeError, BridgeHandler, serve

# The upstream credential this bridge holds. The assistant never sees it.
UPSTREAM_TOKEN = os.environ.get("EXAMPLE_UPSTREAM_TOKEN", "")
# The token the assistant presents to THIS bridge.
BRIDGE_TOKEN = os.environ.get("EXAMPLE_BRIDGE_TOKEN", "")


def echo(handler: BridgeHandler, body: dict[str, Any] | None) -> tuple[int, Any]:
    if not UPSTREAM_TOKEN:
        raise BridgeError(503, "EXAMPLE_UPSTREAM_TOKEN is not configured")
    message = (body or {}).get("message")
    if not isinstance(message, str):
        raise BridgeError(400, "expected string field: message")
    # A real bridge would call the upstream API with UPSTREAM_TOKEN here and
    # return only the safe, allowlisted result.
    return 200, {"echoed": message, "upstream_configured": True}


class ExampleBridge(BridgeHandler):
    server_version = "agentbox-bridge-example/1.0"
    bridge_token = BRIDGE_TOKEN
    routes = {
        ("POST", "/v1/echo"): echo,
    }


if __name__ == "__main__":
    serve(ExampleBridge)
