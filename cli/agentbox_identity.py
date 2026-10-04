"""Identities: one bearer token per person in the gateway env."""
from __future__ import annotations

import re
import secrets
import subprocess
from pathlib import Path

from agentbox_common import env_dir, report

IDENTITY_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")


def identity_env_path() -> Path:
    return Path(env_dir()) / "agentbox-mcp.env"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def write_env_value(path: Path, key: str, value: str) -> None:
    """Rewrite one key in place, preserving order and comments."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, replaced = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    path.chmod(0o600)


def parse_identities(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if entry and ":" in entry:
            name, _, token = entry.partition(":")
            if name.strip() and token.strip():
                out[name.strip()] = token.strip()
    return out


def unit_environment(unit: str) -> dict:
    """Environment of a user unit, as systemd has it loaded.

    Read from systemd, not the repo copy. The installed and checked-in units
    can drift, and this reports on the running household.
    """
    out = {}
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "Environment"],
            capture_output=True, text=True, timeout=20, check=False)
    except Exception:  # noqa: BLE001 (a listing must not fail on this)
        return out
    for chunk in (result.stdout or "").replace("Environment=", "").split():
        key, _, value = chunk.partition("=")
        if key:
            out[key] = value
    return out


def identity_list() -> int:

    values = read_env_file(identity_env_path())
    identities = parse_identities(values.get("AGENTBOX_IDENTITIES", ""))
    if not identities:
        report("WARN", "no identities configured, running single-operator")
        print("\nAdd one with: cli/agentbox identity add <name>")
        return 0

    portal = unit_environment("agentbox-portal")
    approvals = unit_environment("agentbox-approvals")
    admins = {n.strip() for n in portal.get("AGENTBOX_ADMINS", "").split(",")
              if n.strip()}
    emails = dict(pair.split(":", 1) for pair in
                  portal.get("AGENTBOX_IDENTITY_EMAILS", "").split(",")
                  if ":" in pair)
    chat = dict(pair.split(":", 1) for pair in
                (approvals.get("AGENTBOX_DISCORD_IDENTITIES", "")
                 or portal.get("AGENTBOX_DISCORD_IDENTITIES", "")
                 ).replace(" ", ",").split(",") if ":" in pair)
    smtp = bool(portal.get("AGENTBOX_SMTP_HOST"))

    print(f"{len(identities)} identit"
          f"{'y' if len(identities) == 1 else 'ies'}:\n")
    print(f"  {'name':<10} {'role':<7} {'sign in by':<22} own bridges")
    for name in sorted(identities):
        suffix = name.upper().replace("-", "_")
        own = [prefix.lower() for prefix in ("GOOGLE", "VIKUNJA", "MEMORY", "HA")
               if values.get(f"{prefix}_BRIDGE_TOKEN_{suffix}")]
        ways = []
        if smtp and name in emails:
            ways.append("email")
        if name in chat:
            ways.append("Discord DM")
        role = "admin" if name in admins else "member"
        print(f"  {name:<10} {role:<7} "
              f"{', '.join(ways) if ways else 'operator link only':<22} "
              f"{', '.join(own) if own else 'none (all shared)'}")

    unreachable = [n for n in sorted(identities)
                   if n not in chat and not (smtp and n in emails)]
    print("\nMemory: each identity sees their own scope plus 'household'.")
    if unreachable:
        print(f"\n{', '.join(unreachable)} cannot request a link "
              f"themselves. Give them one with: "
              f"cli/agentbox-portal link <name>")
        print("To let them self-serve, set AGENTBOX_DISCORD_IDENTITIES "
              "(no SMTP needed) or AGENTBOX_SMTP_HOST.")
    return 0


def identity_add(name: str) -> int:

    if not IDENTITY_NAME.match(name):
        report("FAIL", "identity must be lowercase letters, digits, dash or "
                     "underscore, starting with a letter")
        return 2
    path = identity_env_path()
    if not path.exists():
        report("FAIL", f"no gateway env at {path}; deploy agentbox-mcp first")
        return 1
    values = read_env_file(path)
    identities = parse_identities(values.get("AGENTBOX_IDENTITIES", ""))
    if name in identities:
        report("FAIL", f"identity '{name}' already exists")
        return 1

    token = secrets.token_hex(32)
    identities[name] = token
    write_env_value(path, "AGENTBOX_IDENTITIES",
              ",".join(f"{n}:{t}" for n, t in sorted(identities.items())))
    report("OK", f"added identity '{name}'")

    first_identity = len(identities) == 1
    print(f"\n  token: {token}\n")
    print("Next:")
    print(f"  1. Store it:  op item edit Agentbox/agentbox --token_{name}='{token}'")
    print("  2. Redeploy:  cli/agentbox deploy agentbox-mcp")
    if first_identity:
        print("\n  NOTE: this is the first identity, so AGENTBOX_MCP_SHARED_TOKEN")
        print("  stops working. Point the Hermes gateway config at this token")
        print("  instead, or it will get 401 on every call.")
    print(f"\n  Optional: give {name} their own Google account rather than "
          f"sharing yours:")
    suffix = name.upper().replace("-", "_")
    print(f"    GOOGLE_BRIDGE_URL_{suffix}=http://{name}-google-bridge:8080")
    print(f"    GOOGLE_BRIDGE_TOKEN_{suffix}=<that bridge's token>")
    print(f"  Without those, {name} shares the existing Google bridge, which "
          f"is\n  correct for shared services and wrong for personal mail.")
    return 0


def identity_remove(name: str) -> int:

    path = identity_env_path()
    values = read_env_file(path)
    identities = parse_identities(values.get("AGENTBOX_IDENTITIES", ""))
    if name not in identities:
        report("FAIL", f"no identity '{name}'")
        return 1
    del identities[name]
    write_env_value(path, "AGENTBOX_IDENTITIES",
              ",".join(f"{n}:{t}" for n, t in sorted(identities.items())))
    report("OK", f"removed identity '{name}'; redeploy agentbox-mcp to apply")
    if not identities:
        report("WARN", "no identities remain, so the gateway falls back to "
                     "AGENTBOX_MCP_SHARED_TOKEN")
    print(f"\nTheir memories are NOT deleted. They are still scoped to "
          f"'{name}'\nand unreachable. Delete them separately if "
          f"that is what you want.")
    return 0
