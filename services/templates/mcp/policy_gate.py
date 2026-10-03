"""Runtime policy enforcement for tool calls, used by agentbox-mcp and every bridge.

One policy covers the operator and the assistant. policies/approval-policy.yaml
lists capabilities under `tiers` and maps each assistant tool to one under
`tools`, so a tool call lands in the same tier as the matching operator action.
Tiers are checked in the order always_denied, approval_required, allowed. A
tool with no mapping is approval_required, so a tool added without one is
refused until someone maps it.

An approval_required tool needs a grant from `cli/agentbox grant <tool>`,
written to a grants file this module reads. Grants expire and are single-use by
default, so an approval cannot quietly become a standing permission.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

POLICY_PATH = Path(os.environ.get("AGENTBOX_RUNTIME_POLICY", "/app/approval-policy.yaml"))
GRANTS_PATH = Path(os.environ.get("AGENTBOX_POLICY_GRANTS", "/policy/grants.json"))
# Writable, and separate from the grants file. Recording that a grant was used
# can only remove permission, so this path carries no authority.
CONSUMED_PATH = Path(os.environ.get("AGENTBOX_POLICY_CONSUMED", "/policy-state/consumed.json"))
# Refused calls are recorded here so the operator is told something is
# waiting. Asking for permission grants none, so this carries no authority
# either.
PENDING_DIR = Path(os.environ.get("AGENTBOX_POLICY_PENDING", "/policy-state/pending"))

ALLOWED = "allowed"
APPROVAL_REQUIRED = "approval_required"
ALWAYS_DENIED = "always_denied"


class PolicyDenied(Exception):
    """Raised when a tool call is refused. The message reaches the assistant."""


def load_tiers(path: Path = POLICY_PATH) -> dict[str, list[str]]:
    """Parse the tier map. The file is flat two-level YAML, and the images use
    only the standard library, so this parses it by hand.
    """
    tiers: dict[str, list[str]] = {}
    current: str | None = None
    in_tiers = False
    if not path.exists():
        return tiers
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.rstrip(":") == "tiers":
            in_tiers = True
            continue
        if not in_tiers:
            continue
        if not line.startswith(" "):
            break
        stripped = line.strip()
        if stripped.endswith((":", ": []")):
            current = stripped.split(":", 1)[0].strip()
            tiers.setdefault(current, [])
            continue
        if stripped.startswith("- ") and current:
            tiers[current].append(stripped[2:].strip())
    return tiers


def load_tool_map(path: Path = POLICY_PATH) -> dict[str, str]:
    """Parse the `tools:` section, which maps a tool name to a capability."""
    mapping: dict[str, str] = {}
    if not path.exists():
        return mapping
    in_tools = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.rstrip(":") == "tools":
            in_tools = True
            continue
        if not in_tools:
            continue
        if not line.startswith(" "):
            break
        if ":" in line:
            tool, _, capability = line.strip().partition(":")
            if capability.strip():
                mapping[tool.strip()] = capability.strip()
    return mapping


def capability_of(tool: str, tool_map: dict[str, str] | None = None) -> str:
    """The capability a tool uses, or "" if it maps to none.

    Used to label outcome records. An unmapped tool shows up as "" rather than
    a default, because that is worth seeing in the log.
    """
    mapping = load_tool_map() if tool_map is None else tool_map
    return mapping.get(tool or "", "")


def tier_of(tool: str, tiers: dict[str, list[str]],
            tool_map: dict[str, str] | None = None) -> str:
    """Resolve a tool's tier through the capability it uses.

    A tool maps to a capability and the capability has the tier. That is what
    lets one policy cover an operator command and an assistant tool. An
    unmapped tool resolves to approval_required, so it fails closed.
    """
    mapping = load_tool_map() if tool_map is None else tool_map
    capability = mapping.get(tool)
    if capability is None:
        return APPROVAL_REQUIRED
    for tier in (ALWAYS_DENIED, APPROVAL_REQUIRED, ALLOWED):
        if capability in tiers.get(tier, []):
            return tier
    return APPROVAL_REQUIRED


def _load_consumed(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_grants(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # An unreadable grants file means no grants, never all of them.
        return []
    return data.get("grants", []) if isinstance(data, dict) else []


def _consumed_key(grant: dict) -> str:
    return f"{grant.get('tool')}@{int(float(grant.get('granted_at', 0)))}"


def consume_grant(tool: str, path: Path = GRANTS_PATH, now: float | None = None,
                  consumed_path: Path | None = None, consume: bool = True,
                  tool_map: dict[str, str] | None = None,
                  identity: str | None = None) -> bool:
    """Return True if an unexpired, unused grant covers `tool`.

    Grants are mounted read-only so a compromised server cannot give itself
    permission. Using a grant therefore cannot delete it from that file, and is
    recorded in a separate writable place instead, which can only ever remove
    permission.

    If that record cannot be written, the call is refused. Otherwise a
    single-use grant would quietly work until it expired.
    """
    store = CONSUMED_PATH if consumed_path is None else consumed_path
    stamp = time.time() if now is None else now
    consumed = _load_consumed(store)
    mapping = load_tool_map() if tool_map is None else tool_map
    for grant in _load_grants(path):
        granted = grant.get("tool")
        # A grant names a tool, but a bridge asks by capability. Accept either,
        # so `agentbox grant archive_gmail` covers the email_state_change the
        # bridge checks.
        if granted != tool and mapping.get(granted) != tool:
            continue
        # A grant can be scoped to one person (`agentbox grant --for alex`).
        # An unscoped grant covers anyone.
        scoped_to = grant.get("identity")
        if scoped_to is not None and scoped_to != identity:
            continue
        if float(grant.get("expires_at", 0)) <= stamp:
            continue
        if not grant.get("single_use", True):
            return True
        if not consume:
            return True
        key = _consumed_key(grant)
        if key in consumed:
            continue
        try:
            store.parent.mkdir(parents=True, exist_ok=True)
            consumed[key] = int(stamp)
            store.write_text(json.dumps(consumed), encoding="utf-8")
        except OSError:
            raise PolicyDenied(
                f"'{tool}' has a single-use grant but the consumption record at "
                f"{store} is not writable, so it cannot be enforced. Refusing "
                f"rather than treating it as unlimited.") from None
        return True
    return False


def check_capability(capability: str, tiers: dict[str, list[str]] | None = None,
                     grants_path: Path = GRANTS_PATH,
                     consumed_path: Path | None = None,
                     consume: bool = True, subject: str | None = None,
                     tool_map: dict[str, str] | None = None,
                     identity: str | None = None) -> None:
    """Raise PolicyDenied unless `capability` may be used now.

    Used by the bridges, which know the capability rather than a tool name.
    `consume=False` checks without using up a single-use grant, so an earlier
    layer can refuse early and leave the grant for the bridge.
    """
    resolved = load_tiers() if tiers is None else tiers
    label = subject or capability
    tier = ALLOWED if capability in resolved.get(ALLOWED, []) else (
        ALWAYS_DENIED if capability in resolved.get(ALWAYS_DENIED, []) else APPROVAL_REQUIRED)
    if tier == ALLOWED:
        return
    if tier == ALWAYS_DENIED:
        raise PolicyDenied(
            f"'{label}' is always denied by policy and cannot be approved at runtime. "
            f"Only a human, outside the assistant, may do this.")
    if consume_grant(capability, grants_path, consumed_path=consumed_path,
                     consume=consume, tool_map=tool_map, identity=identity):
        return
    raise PolicyDenied(
        f"'{label}' ({capability}) requires operator approval and no grant is "
        f"active. Ask the operator to run: agentbox grant {label} --ttl 15m")


def record_pending(tool: str, capability: str | None, path: Path | None = None,
                   identity: str | None = None) -> None:
    """Note that a call was refused for lack of approval.

    One request per tool, so a model that retries does not queue five. It never
    raises, because a failure to record must not turn a clean refusal into an
    error.
    """
    store = PENDING_DIR if path is None else path
    try:
        store.mkdir(parents=True, exist_ok=True)
        entry = store / f"{tool}.json"
        if entry.exists():
            return
        record = {
            "tool": tool,
            "capability": capability,
            "requested_at": int(time.time()),
        }
        if identity:
            # Whose call it was, so the approval answers the right person.
            record["identity"] = identity
        entry.write_text(json.dumps(record), encoding="utf-8")
    except OSError:
        pass


def check(tool: str, tiers: dict[str, list[str]] | None = None,
          grants_path: Path = GRANTS_PATH,
          consumed_path: Path | None = None,
          tool_map: dict[str, str] | None = None,
          consume: bool = True, identity: str | None = None) -> None:
    """Raise PolicyDenied unless `tool` may run now."""
    resolved = load_tiers() if tiers is None else tiers
    tier = tier_of(tool, resolved, tool_map)
    if tier == ALLOWED:
        return
    if tier == ALWAYS_DENIED:
        raise PolicyDenied(
            f"'{tool}' is always denied by policy and cannot be approved at runtime. "
            f"Only a human, outside the assistant, may do this.")
    if consume_grant(tool, grants_path, consumed_path=consumed_path,
                     consume=consume, tool_map=tool_map, identity=identity):
        return
    mapping = load_tool_map() if tool_map is None else tool_map
    capability = mapping.get(tool)
    record_pending(tool, capability, identity=identity)
    raise PolicyDenied(
        f"'{tool}' ({capability or 'no mapped capability'}) requires operator "
        f"approval. The operator has been notified and can approve it; ask them "
        f"to check, then try again.")
