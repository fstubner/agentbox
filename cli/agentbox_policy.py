"""The approval policy from the operator side: lookups, sync to the mount, grants."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import agentbox_status
from agentbox_common import FAIL, OK, REPO, WARN, report

# --- policy ---------------------------------------------------------------


def source_sha(service: str) -> str:
    """Delegates to agentbox_status, the one definition, which the portal
    also uses."""
    return agentbox_status.source_sha(service)


def load_policy() -> dict:
    """Parse policies/approval-policy.yaml without external deps (flat 2-level lists)."""
    path = REPO / "policies" / "approval-policy.yaml"
    tiers: dict[str, list[str]] = {}
    current: str | None = None
    in_tiers = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line == "tiers:":
            in_tiers = True
            continue
        if in_tiers:
            m = re.match(r"^  (\w+):\s*$", line)
            if m:
                current = m.group(1)
                tiers[current] = []
                continue
            m = re.match(r"^    - (\S+)", line)
            if m and current:
                tiers[current].append(m.group(1))
                continue
            if not line.startswith("  "):
                in_tiers = False
    return tiers


def policy_check(action: str) -> int:
    tiers = load_policy()
    for tier in ("always_denied", "approval_required", "allowed"):
        if action in tiers.get(tier, []):
            print(json.dumps({"action": action, "tier": tier}))
            return 2 if tier == "always_denied" else 0
    # deny-by-default: unknown actions require approval
    print(json.dumps({"action": action, "tier": "approval_required", "reason": "unknown_action_default"}))
    return 0


def policy_dir() -> Path:
    return Path(os.environ.get(
        "AGENTBOX_POLICY_DIR",
        str(Path("~/.local/state/agentbox/policy").expanduser())))


def policy_sync(quiet: bool = False) -> int:
    """Copy the committed approval policy onto the read-only bridge mount.

    The policy is read live from this mount, like grants, so changing it does
    not mean rebuilding every image. `policy_drift` checks that what is
    enforced matches what is committed.

    The directory is owned by the operator and mounted read-only into every
    bridge.
    """
    source = REPO / "policies" / "approval-policy.yaml"
    target = policy_dir() / "approval-policy.yaml"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = source.read_bytes()
        if target.exists() and target.read_bytes() == payload:
            if not quiet:
                report(OK, "runtime policy already current")
            return 0
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(payload)
        # Readable by the containers that mount this; not writable by them.
        tmp.chmod(0o644)
        tmp.replace(target)
    except OSError as exc:
        report(FAIL, f"could not sync runtime policy: {exc}")
        return 1
    if not quiet:
        report(OK, f"runtime policy synced to {target}")
    return 0


def policy_drift() -> int:
    """Whether the policy the bridges enforce matches the committed one,
    compared byte for byte."""
    source = REPO / "policies" / "approval-policy.yaml"
    target = policy_dir() / "approval-policy.yaml"
    try:
        committed = source.read_bytes()
    except OSError as exc:
        report(FAIL, f"cannot read the committed policy: {exc}")
        return 1
    if not target.exists():
        # Bridges fall back to the copy baked into the image, so this is a
        # warning rather than an outage. It is still reported, because policy
        # edits are not taking effect and nothing else says so.
        report(WARN, f"no runtime policy at {target}; bridges are enforcing "
                     f"the copy baked into their images. Run "
                     f"`cli/agentbox policy sync`")
        return 0
    if target.read_bytes() != committed:
        report(FAIL, f"runtime policy differs from the committed one "
                     f"({target}); bridges are enforcing something that is "
                     f"not in git. Run `cli/agentbox policy sync`")
        return 1
    report(OK, "runtime policy matches the committed one")
    return 0


# --- runtime policy grants --------------------------------------------------
#
# An approval_required tool is refused until an operator issues a grant. Grants
# are time-boxed and single-use by default so an approval cannot become a
# standing permission.

GRANTS_PATH = Path(os.environ.get(
    "AGENTBOX_POLICY_GRANTS",
    str(Path("~/.local/state/agentbox/policy/grants.json").expanduser())))


def load_tool_map() -> dict[str, str]:
    """Assistant tool -> capability, from the single approval policy."""
    path = REPO / "policies" / "approval-policy.yaml"
    mapping: dict[str, str] = {}
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


def load_runtime_tiers() -> dict[str, list[str]]:
    path = REPO / "policies" / "approval-policy.yaml"
    tiers: dict[str, list[str]] = {}
    current = None
    in_tiers = False
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
        elif stripped.startswith("- ") and current:
            tiers[current].append(stripped[2:].strip())
    return tiers


def parse_ttl(value: str) -> int:
    units = {"s": 1, "m": 60, "h": 3600}
    if value and value[-1] in units:
        return int(float(value[:-1]) * units[value[-1]])
    return int(value)


OUTCOME_DIR = Path(os.environ.get(
    "AGENTBOX_LOG_DIR", str(Path("~/.local/state/agentbox/logs").expanduser())))


def record_decision(actor: str, action: str, subject: str, detail: str = "") -> None:
    """Append an operator decision to the outcome journal.

    These are the journal's only records of a person's judgement, such as
    approving something nine times out of nine. They go in the same directory
    agentbox-mcp writes to, so `review_own_activity` sees both what the
    assistant tried and what the operator decided.
    """
    path = OUTCOME_DIR / "operator-outcomes.jsonl"
    entry = {"ts": int(time.time()), "actor": actor,
             "action": action, "subject": subject}
    if detail:
        entry["detail"] = detail
    try:
        OUTCOME_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError as exc:
        # Never fail the decision because we could not record it.
        report(WARN, f"could not record decision: {exc}")


def grant(tool: str, ttl: str, repeatable: bool, identity: str = "") -> int:
    tiers = load_runtime_tiers()
    capability = load_tool_map().get(tool)
    if capability is None:
        report(WARN, f"'{tool}' maps to no capability; it defaults to approval_required")
    elif capability in tiers.get("always_denied", []):
        report(FAIL, f"'{tool}' ({capability}) is always_denied and cannot be granted")
        return 2
    elif capability in tiers.get("allowed", []):
        report(WARN, f"'{tool}' ({capability}) is already allowed; no grant needed")
        return 0
    seconds = parse_ttl(ttl)
    GRANTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if GRANTS_PATH.exists():
        try:
            existing = json.loads(GRANTS_PATH.read_text()).get("grants", [])
        except ValueError:
            existing = []
    now = int(time.time())
    existing = [g for g in existing if float(g.get("expires_at", 0)) > now]
    record = {"tool": tool, "expires_at": now + seconds,
              "single_use": not repeatable, "granted_at": now}
    if identity:
        # Scoped: covers only this identity. Unscoped covers anyone, which is
        # the single-operator behaviour every pre-identity grant has.
        record["identity"] = identity
    existing.append(record)
    GRANTS_PATH.write_text(json.dumps({"grants": existing}, indent=2), encoding="utf-8")
    GRANTS_PATH.chmod(0o644)
    scope = "repeatable" if repeatable else "single-use"
    if identity:
        scope += f", for {identity}"
    record_decision("operator", "approve", tool, scope)
    report(OK, f"granted {tool} for {seconds}s ({scope})")
    return 0


PENDING_DIR = Path(os.environ.get(
    "AGENTBOX_POLICY_PENDING",
    str(Path("~/.local/state/agentbox/policy-state/pending").expanduser())))


def approvals_list() -> int:
    """What the assistant asked for and could not do."""
    if not PENDING_DIR.is_dir():
        print("nothing pending")
        return 0
    entries = sorted(PENDING_DIR.glob("*.json"))
    if not entries:
        print("nothing pending")
        return 0
    now = int(time.time())
    for entry in entries:
        try:
            record = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        age = now - int(record.get("requested_at", now))
        print(f"{record.get('tool'):28} {record.get('capability') or '(unmapped)':28} "
              f"{age // 60}m ago")
    print("\napprove with: agentbox grant <tool> --ttl 15m")
    return 0


def approvals_clear(tool: str | None) -> int:
    if not PENDING_DIR.is_dir():
        print("nothing pending")
        return 0
    targets = [PENDING_DIR / f"{tool}.json"] if tool else list(PENDING_DIR.glob("*.json"))
    cleared = 0
    for target in targets:
        if target.exists():
            target.unlink()
            cleared += 1
    report(OK, f"cleared {cleared} pending request(s)")
    return 0


def grants_list() -> int:
    now = int(time.time())
    if not GRANTS_PATH.exists():
        print("no grants issued")
        return 0
    try:
        entries = json.loads(GRANTS_PATH.read_text()).get("grants", [])
    except ValueError:
        report(FAIL, f"grants file is not valid JSON: {GRANTS_PATH}")
        return 1
    active = [g for g in entries if float(g.get("expires_at", 0)) > now]
    if not active:
        print("no active grants")
        return 0
    for g in active:
        left = int(float(g["expires_at"]) - now)
        scope = "single-use" if g.get("single_use", True) else "repeatable"
        print(f"{g['tool']:28} {left:5d}s left  ({scope})")
    return 0


def grants_revoke(tool: str | None) -> int:
    if not GRANTS_PATH.exists():
        print("no grants to revoke")
        return 0
    try:
        entries = json.loads(GRANTS_PATH.read_text()).get("grants", [])
    except ValueError:
        entries = []
    remaining = [g for g in entries if tool and g.get("tool") != tool]
    GRANTS_PATH.write_text(json.dumps({"grants": remaining}, indent=2), encoding="utf-8")
    report(OK, f"revoked {'all grants' if not tool else tool}")
    return 0
