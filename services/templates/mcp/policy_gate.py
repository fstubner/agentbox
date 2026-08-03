"""Runtime policy enforcement for MCP tool calls.

The architecture diagram has always drawn a policy engine, but nothing outside
cli/agentbox read the policy — it was an operator/CI check, so at runtime the
assistant's tool calls were ungated. This module is the missing half.

One policy governs both actors. policies/approval-policy.yaml lists
capabilities under `tiers` and maps each assistant tool onto one under `tools`,
so a tool call resolves to the same tier as the equivalent operator action.
Semantics: always_denied -> approval_required -> allowed, and a tool that maps
to no capability defaults to approval_required. Deny-by-default is the point;
a tool added without being mapped is refused until someone maps it.

An approval_required tool needs an operator grant, issued out of band with
`cli/agentbox grant <tool> --ttl 15m` and written to a grants file this module
reads. Grants are time-boxed and single-use by default, so an approval cannot
silently become a standing permission.

Copy this file verbatim into a new MCP's app/ directory alongside
approval-policy.yaml; do not edit it per-service. `cli/agentbox validate`
fails on drift in either.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

POLICY_PATH = Path(os.environ.get("AGENTBOX_RUNTIME_POLICY", "/app/approval-policy.yaml"))
GRANTS_PATH = Path(os.environ.get("AGENTBOX_POLICY_GRANTS", "/policy/grants.json"))
# Writable, and deliberately NOT the grants file: recording a consumption can
# only remove permission, so this path carries no authority.
CONSUMED_PATH = Path(os.environ.get("AGENTBOX_POLICY_CONSUMED", "/policy-state/consumed.json"))
# Denied calls are recorded here so the operator can be told something is
# waiting, rather than discovering it when they next read the conversation.
# Same writable path as consumption records: writing a request for permission
# confers none, so this carries no authority either.
PENDING_DIR = Path(os.environ.get("AGENTBOX_POLICY_PENDING", "/policy-state/pending"))

ALLOWED = "allowed"
APPROVAL_REQUIRED = "approval_required"
ALWAYS_DENIED = "always_denied"


class PolicyDenied(Exception):
    """Raised when a tool call is refused. The message reaches the assistant."""


def load_tiers(path: Path = POLICY_PATH) -> dict[str, list[str]]:
    """Parse the runtime tier map. Flat two-level YAML, no external deps —
    the MCP images are stdlib-only and a policy file is not worth a dependency.
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
        if stripped.endswith(":") or stripped.endswith(": []"):
            current = stripped.split(":", 1)[0].strip()
            tiers.setdefault(current, [])
            continue
        if stripped.startswith("- ") and current:
            tiers[current].append(stripped[2:].strip())
    return tiers


def load_tool_map(path: Path = POLICY_PATH) -> dict[str, str]:
    """Parse the `tools:` section — assistant tool name -> capability."""
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
    """The capability a tool exercises, or "" if it maps to none.

    For labelling an outcome record. Deliberately does not fall back to a tier:
    an unmapped tool is a real condition worth seeing in the log rather than
    something to paper over with a default.
    """
    mapping = load_tool_map() if tool_map is None else tool_map
    return mapping.get(tool or "", "")


def tier_of(tool: str, tiers: dict[str, list[str]],
            tool_map: dict[str, str] | None = None) -> str:
    """Resolve a tool's tier through the capability it exercises.

    A tool is not itself a policy entry — it maps onto a capability, and the
    capability carries the tier. That indirection is what lets one policy cover
    both an operator running a command and the assistant calling a tool.

    Unmapped tools resolve to no capability and therefore to approval_required,
    so a tool added without being mapped fails closed rather than running.
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
        # An unreadable grants file must not become an open door.
        return []
    return data.get("grants", []) if isinstance(data, dict) else []


def _consumed_key(grant: dict) -> str:
    return f"{grant.get('tool')}@{int(float(grant.get('granted_at', 0)))}"


def consume_grant(tool: str, path: Path = GRANTS_PATH, now: float | None = None,
                  consumed_path: Path | None = None, consume: bool = True,
                  tool_map: dict[str, str] | None = None) -> bool:
    """Return True if an unexpired, unconsumed grant covers `tool`.

    Grants are mounted read-only so a compromised MCP cannot issue itself
    permission. Consumption therefore cannot delete from the grants file, and is
    recorded separately in a writable location instead. That split is the point:
    writing a consumption record can only ever *remove* permission, so the
    writable path carries no authority.

    If the consumption record cannot be written, the call is refused rather than
    allowed. An unenforceable single-use grant that silently behaves as
    unlimited is worse than a failed call — this exact case shipped once, where
    a read-only mount turned every single-use grant into a TTL-long window.
    """
    store = CONSUMED_PATH if consumed_path is None else consumed_path
    stamp = time.time() if now is None else now
    consumed = _load_consumed(store)
    mapping = load_tool_map() if tool_map is None else tool_map
    for grant in _load_grants(path):
        granted = grant.get("tool")
        # A grant names a tool; a bridge asks by capability. Accept either, so
        # `agentbox grant archive_gmail` authorises the email_state_change the
        # bridge sees.
        if granted != tool and mapping.get(granted) != tool:
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
                f"rather than treating it as unlimited.")
        return True
    return False


def check_capability(capability: str, tiers: dict[str, list[str]] | None = None,
                     grants_path: Path = GRANTS_PATH,
                     consumed_path: Path | None = None,
                     consume: bool = True, subject: str | None = None,
                     tool_map: dict[str, str] | None = None) -> None:
    """Raise PolicyDenied unless `capability` may be exercised now.

    Used by the bridges, which know the capability directly rather than a tool
    name. `consume=False` checks without spending a single-use grant, so a
    caller can deny early without stealing the grant from the layer whose
    answer is authoritative.
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
                     consume=consume, tool_map=tool_map):
        return
    raise PolicyDenied(
        f"'{label}' ({capability}) requires operator approval and no grant is "
        f"active. Ask the operator to run: agentbox grant {label} --ttl 15m")


def record_pending(tool: str, capability: str | None, path: Path | None = None) -> None:
    """Note that a call was refused for want of approval.

    Best-effort and idempotent per tool: a model that retries should not queue
    five identical requests. Never raises — a failure to record must not turn a
    clean policy denial into an error the model has to interpret.
    """
    store = PENDING_DIR if path is None else path
    try:
        store.mkdir(parents=True, exist_ok=True)
        entry = store / f"{tool}.json"
        if entry.exists():
            return
        entry.write_text(json.dumps({
            "tool": tool,
            "capability": capability,
            "requested_at": int(time.time()),
        }), encoding="utf-8")
    except OSError:
        pass


def check(tool: str, tiers: dict[str, list[str]] | None = None,
          grants_path: Path = GRANTS_PATH,
          consumed_path: Path | None = None,
          tool_map: dict[str, str] | None = None,
          consume: bool = True) -> None:
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
                     consume=consume, tool_map=tool_map):
        return
    mapping = load_tool_map() if tool_map is None else tool_map
    capability = mapping.get(tool)
    record_pending(tool, capability)
    raise PolicyDenied(
        f"'{tool}' ({capability or 'no mapped capability'}) requires operator "
        f"approval. The operator has been notified and can approve it; ask them "
        f"to check, then try again.")
