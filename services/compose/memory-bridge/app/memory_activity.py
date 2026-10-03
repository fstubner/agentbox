"""Self-reflection: a summary of what the assistant tried and how it went,
read from the outcome journals."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from bridge_base import BridgeError
from memory_store import first, query_of

try:
    import policy_gate
except ImportError:  # no policy mounted: the summary omits tiers rather than fails
    policy_gate = None  # type: ignore[assignment]

# --- self-reflection --------------------------------------------------------
#
# Reads the outcome journals agentbox-mcp writes and returns an aggregate, so
# reflection works from a record rather than from the model's recollection of a
# conversation. It returns counts and rates, never journal lines. "archive_gmail
# was refused six times" is something to act on, and a replay of six refusals
# is not.

LOG_DIR = Path(os.environ.get("BRIDGE_LOG_DIR", "/logs"))


def read_outcomes(since_days: int) -> list[dict[str, Any]]:
    cutoff = time.time() - since_days * 86400
    records: list[dict[str, Any]] = []
    if not LOG_DIR.is_dir():
        return records
    for path in sorted(LOG_DIR.glob("*-outcomes.jsonl")):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue  # a torn final line is normal for an append log
                if record.get("ts", 0) >= cutoff:
                    records.append(record)
        except OSError:
            continue
    return records


def activity(handler, body):
    """Aggregate what the assistant has tried, and how it went."""
    query = query_of(handler)
    try:
        days = max(1, min(90, int(first(query, "days", "7"))))
    except ValueError:
        raise BridgeError(400, "days must be an integer") from None

    records = read_outcomes(days)
    calls = [r for r in records if r.get("tool")]
    decisions = [r for r in records if r.get("action")]

    tools: dict[str, dict[str, Any]] = {}
    for record in calls:
        name = record["tool"]
        entry = tools.setdefault(name, {"calls": 0, "ok": 0, "error": 0,
                                        "denied": 0, "invalid": 0, "ms": []})
        entry["calls"] += 1
        outcome = record.get("outcome", "")
        if outcome in entry:
            entry[outcome] += 1
        if isinstance(record.get("ms"), (int, float)):
            entry["ms"].append(record["ms"])

    # Each tool's tier, because "denied" alone is ambiguous. Without it, a
    # refused approval_required tool looks like one that is never allowed, and
    # reflection concludes it should stop asking.
    tiers = {}
    if policy_gate is not None:
        try:
            loaded = policy_gate.load_tiers()
            tool_map = policy_gate.load_tool_map()
            for name in tools:
                capability = tool_map.get(name)
                for tier in ("always_denied", "approval_required", "allowed"):
                    if capability in loaded.get(tier, []):
                        tiers[name] = tier
                        break
                else:
                    tiers[name] = "approval_required"  # unmapped fails closed
        except Exception:  # noqa: BLE001 (a summary must not fail on policy)
            tiers = {}

    summary = {}
    for name, entry in sorted(tools.items()):
        durations = sorted(entry.pop("ms"))
        if durations:
            entry["median_ms"] = durations[len(durations) // 2]
        if name in tiers:
            entry["tier"] = tiers[name]
            if tiers[name] == "approval_required":
                entry["note"] = "refused without a grant; the operator can approve it"
            elif tiers[name] == "always_denied":
                entry["note"] = "never permitted; do not ask"
            elif tiers[name] == "allowed" and entry.get("denied"):
                # A refused `allowed` tool is not broken. The bridge is saying
                # something is not set up, such as an entity not on the
                # allowlist or a protected file. Said explicitly so reflection
                # does not conclude the tool is broken.
                entry["note"] = ("permitted, but the bridge refused: usually "
                                 "not configured (an entity not on the "
                                 "operator's allowlist, a guarded path) rather "
                                 "than broken. Ask what to add, do not "
                                 "conclude it is unusable")
        summary[name] = entry

    # Error classes, not messages, because an upstream message quotes its input.
    problems: dict[str, int] = {}
    for record in calls:
        if record.get("outcome") in ("error", "invalid") and record.get("detail"):
            key = f"{record['tool']}: {record['detail']}"
            problems[key] = problems.get(key, 0) + 1

    verdicts: dict[str, dict[str, int]] = {}
    for record in decisions:
        subject = record.get("subject", "")
        entry = verdicts.setdefault(subject, {})
        action = record.get("action", "")
        entry[action] = entry.get(action, 0) + 1

    return 200, {
        "window_days": days,
        "total_calls": len(calls),
        "tools": summary,
        "problems": dict(sorted(problems.items(), key=lambda kv: -kv[1])[:20]),
        "operator_decisions": verdicts,
        # Said plainly, so an empty window does not read as evidence of good
        # behaviour.
        "note": ("No activity recorded in this window."
                 if not calls else
                 "Counts only. Journal lines are never returned."),
    }
