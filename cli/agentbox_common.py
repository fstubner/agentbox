"""Shared basics for the agentbox CLI: paths, report levels, env files."""
from __future__ import annotations

import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(os.environ.get("AGENTBOX_REPO", Path(__file__).resolve().parent.parent))


OK, WARN, FAIL = "OK", "WARN", "FAIL"


def report(level: str, msg: str) -> None:
    print(f"[{level.lower()}] {msg}")


def probe(url: str, timeout: float = 5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except Exception:
        return False


# The gateway user, whose permissions `doctor` checks. If the assistant's
# containment breaks, nothing errors, so it has to be checked.
GATEWAY_USER = os.environ.get("AGENTBOX_GATEWAY_USER", "agentbox")
# os.path.expanduser rather than Path.expanduser, which raises when the user
# does not exist. This runs at import, so the CLI would fail on any machine
# without a gateway user, CI included.
GATEWAY_HOME = Path(os.environ.get("AGENTBOX_GATEWAY_HOME",
                                   os.path.expanduser("~agentbox")))


def agent_can(action: str, path: str) -> bool | None:
    """Can the gateway user do `action` (`-w` / `-r`) to `path`? None if unknown."""
    result = subprocess.run(["sudo", "-n", "-u", GATEWAY_USER, "test", action, path],
                            capture_output=True, timeout=10)
    # sudo failing for lack of a password says nothing about the path.
    if result.returncode not in (0, 1):
        return None
    return result.returncode == 0


def env_dir() -> str:
    return os.environ.get("AGENTBOX_ENV_DIR", str(Path("~/.config/agentbox").expanduser()))


def service_env_values(service: str, keys: tuple[str, ...]) -> dict[str, str]:
    """Read selected values from a service env file, resolving op:// refs."""
    directory = Path(env_dir())
    env_file = directory / f"{service}.env"
    if not env_file.exists():
        return {}
    raw: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            if key.strip() in keys:
                raw[key.strip()] = value.strip().strip("'\"")
    if not any(v.startswith("op://") for v in raw.values()):
        return raw
    op_env = dict(os.environ)
    auth_file = directory / "1password.env"
    if auth_file.exists() and not op_env.get("OP_SERVICE_ACCOUNT_TOKEN"):
        for line in auth_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                op_env.setdefault(key.strip(), value.strip())
    resolved = {}
    for key, value in raw.items():
        if not value.startswith("op://"):
            resolved[key] = value
            continue
        out = subprocess.run(["op", "read", value], capture_output=True, text=True,
                             timeout=30, env=op_env, stdin=subprocess.DEVNULL)
        if out.returncode != 0:
            report(FAIL, f"could not resolve {key}: {out.stderr.strip()}")
            return {}
        resolved[key] = out.stdout.strip()
    return resolved


def policy_state_dir() -> str:
    return os.environ.get("AGENTBOX_POLICY_STATE_DIR",
                          str(Path("~/.local/state/agentbox/policy-state").expanduser()))
