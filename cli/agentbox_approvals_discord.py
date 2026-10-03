"""Talking to Discord as the operator's bot, and the approval settings the portal writes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import agentbox_settings

REPO = Path(os.environ.get("AGENTBOX_REPO", Path(__file__).resolve().parent.parent))
API = "https://discord.com/api/v10"


def op_read(reference: str) -> str:
    env = dict(os.environ)
    if not env.get("OP_SERVICE_ACCOUNT_TOKEN"):
        for candidate in ("~/.config/agentbox/1password.env",
                          "~/.config/agentbox/1password.env"):
            path = Path(candidate).expanduser()
            if path.is_file():
                for line in path.read_text(encoding="utf-8").splitlines():
                    if "=" in line and not line.lstrip().startswith("#"):
                        key, _, value = line.partition("=")
                        env.setdefault(key.strip(), value.strip())
                break
    out = subprocess.run(["op", "read", reference], capture_output=True, text=True,
                         timeout=30, env=env, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        sys.exit(f"could not read {reference}: {out.stderr.strip()}")
    return out.stdout.strip()


def discord(method: str, path: str, token: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "agentbox-approvals/1.0",
    })
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        print(f"[warn] discord {method} {path} -> {exc.code}: {detail}", flush=True)
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] discord unreachable: {type(exc).__name__}", flush=True)
        return None


def log(message: str) -> None:
    print(f"agentbox-approvals: {message}", flush=True)


def short_id(value: str) -> str:
    """First eight characters, short enough to type in a chat reply."""
    return str(value)[:8]


def dm_channel(user_id: str, token: str) -> str:
    created = discord("POST", "/users/@me/channels", token,
                      {"recipient_id": user_id})
    return str((created or {}).get("id", ""))


PORTAL_DIR = Path(os.environ.get(
    "AGENTBOX_PORTAL_DIR",
    str(Path("~/.local/state/agentbox/portal").expanduser())))


CHAT_LINKS = PORTAL_DIR / "chat-links.json"
LINK_CURSOR = PORTAL_DIR / "link-requests.json"

SETTINGS = agentbox_settings.SettingsStore(directory=PORTAL_DIR)


def approval_channel() -> str:
    """The channel to ask in. Portal setting, then the legacy env var."""
    return SETTINGS.value("approval_channel").strip()


def approval_operators() -> set[str]:
    """Discord ids whose replies this loop acts on.

    The portal setting comes first. The 1Password value is a fallback for older
    setups, read only when nothing is set locally.
    """
    configured = SETTINGS.value("approval_user_ids")
    if not configured:
        configured = _op_operators()
    return {u.strip() for u in configured.replace(",", " ").split() if u.strip()}


_op_operators_cache: str | None = None


def _op_operators() -> str:
    """The 1Password fallback, read at most once per process.

    This runs in the poll loop and `op` starts a subprocess, so it is not read
    on every pass. Setting the ids on the Operations page takes effect without
    a restart.
    """
    global _op_operators_cache
    if _op_operators_cache is None:
        _op_operators_cache = op_read("op://Agentbox/discord/allowed_users") or ""
    return _op_operators_cache
