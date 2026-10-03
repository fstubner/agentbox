"""The rules evaluator, which makes approved rules fire.

`rules.py` defines and validates rules. This feeds them events and runs their
actions, so an approved rule actually fires for the sources fed here.

## Sources

Two are live, because both can be observed from inside agentbox-mcp without
new credentials.

- **schedule** sends one tick per pass, `{"source": "schedule", "kind":
  "tick", "at": "HH:MM"}`. A rule matching `at: "07:30"` fires once that
  minute, with the cooldown making "once" true.
- **homeassistant** sends entity state changes, polled through the same bridge
  and lean view the assistant uses, `{"source": "homeassistant", "kind":
  "state_change", "entity_id", "state", "previous_state"}`. The first poll
  only records a baseline, so a restart cannot replay the whole house.

gmail, calendar and vikunja are valid in the grammar but have no feed. `rules
approve` says which sources are live, so nobody believes a mail rule will
fire.

## Firing

Each `do` is an ordinary tool call. It passes the same `policy_gate.check` as
the assistant's own calls, without using up a grant, so the bridge's check is
the one that counts. It runs as the rule's identity through the same context
variable a session uses, and is written to the outcome journal under
`service: "agentbox-rules"` with the rule's name in `detail`. A refusal is
recorded, not retried.

## Where approval lives

Rule files sit on `/policy-state`, the mount the container can write and
where writing must never grant anything. An `active` flag in the file would
let a compromised server approve its own rule, or rewrite an approved rule's
actions. So approval is an entry in `/policy/rules-approved.json` on the
read-only operator mount, as with grants, and it pins a fingerprint of the
rule's executing fields. Editing an approved rule voids its approval.

## Limits

- **Only approved, unmodified rules fire.** `matches()` does not decide that,
  since its `enabled` field is true on every proposal.
- **One firing per rule per COOLDOWN_SECONDS** (default 300), so a tick that
  matches twice or a flapping sensor causes one action.
- **No exception escapes.** A broken rule, an unreachable bridge or a failed
  action is logged and skipped.

The baseline and cooldowns are kept in memory, so after a restart a rule can
fire up to one cooldown early. Persisting them would add a writable file for
little benefit.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

import outcome_log
import policy_gate
import rules as grammar

RULES_DIR = Path(os.environ.get("AGENTBOX_RULES_DIR", "/policy-state/rules"))
# The operator-owned approval record, on the read-only mount. cli/agentbox
# writes it; nothing in this container can.
APPROVED_PATH = Path(os.environ.get("AGENTBOX_RULES_APPROVED",
                                    "/policy/rules-approved.json"))
ENABLED = os.environ.get("AGENTBOX_RULES_EVALUATOR", "1") != "0"
INTERVAL_SECONDS = int(os.environ.get("AGENTBOX_RULES_INTERVAL", "30"))
COOLDOWN_SECONDS = int(os.environ.get("AGENTBOX_RULES_COOLDOWN", "300"))
SERVICE = "agentbox-rules"

# The sources this evaluator actually feeds. Exported so the CLI and the
# rulebook tool can say honestly which rules are live rather than repeating a
# blanket claim that goes stale.
LIVE_SOURCES = ("schedule", "homeassistant")

# entity_id -> state from the previous poll. Module-level so tests can reset.
_ha_baseline: dict[str, str] | None = None
_last_fired: dict[str, float] = {}


# What an approval pins: the fields that execute and say as whom, not the
# description or timestamps. cli/agentbox_rules.py repeats this because it
# cannot import container code, and tests/test_rules_evaluator.py checks the
# two agree.
FINGERPRINT_FIELDS = ("name", "identity", "when", "if", "do")


def fingerprint(record: dict) -> str:
    core = {key: record.get(key) for key in FINGERPRINT_FIELDS}
    return hashlib.sha256(json.dumps(
        core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def approvals(path: Path = APPROVED_PATH) -> dict[str, str]:
    """Map of rule name to approved fingerprint. Unreadable means nothing is
    approved, as with an unreadable grants file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for entry in data.get("approved", []) if isinstance(data, dict) else []:
        name, sha = str(entry.get("name", "")), str(entry.get("sha", ""))
        if name and sha:
            out[name] = sha
    return out


def active_rules(directory: Path = RULES_DIR,
                 approved_path: Path = APPROVED_PATH) -> list[dict]:
    """Stored rules the operator approved, unchanged since approval.

    Approval comes only from the approvals file on the read-only mount, never
    from the rule record on the writable one. Comparing fingerprints means an
    edited rule loses its approval.
    """
    approved = approvals(approved_path)
    found = []
    if not directory.is_dir() or not approved:
        return found
    for path in sorted(directory.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if approved.get(str(record.get("name"))) == fingerprint(record):
            found.append(record)
    return found


def schedule_event(now: datetime.datetime | None = None) -> dict:
    stamp = now or datetime.datetime.now()
    return {"source": "schedule", "kind": "tick",
            "at": stamp.strftime("%H:%M")}


def _poll_entities() -> dict[str, str]:
    """Current entity states through the HA bridge, as {entity_id: state}.

    Lazy import: the integrations package builds bridge clients at import
    time, and tests exercise this module without a gateway around it.
    """
    from integrations import _client, homeassistant
    # The HA bridge is shared, not per-identity; make sure a poll never rides
    # on whatever identity the serving thread last set.
    _client.CURRENT_IDENTITY.set("")
    payload = homeassistant.bridge_request(
        "GET", "/v1/entities", query={"view": "lean", "limit": 1000})
    states: dict[str, str] = {}
    for entity in (payload or {}).get("entities", []):
        entity_id = str(entity.get("entity_id") or "")
        if entity_id:
            states[entity_id] = str(entity.get("state"))
    return states


def homeassistant_events() -> list[dict]:
    """State changes since the last poll. First poll baselines silently."""
    global _ha_baseline
    current = _poll_entities()
    previous, _ha_baseline = _ha_baseline, current
    if previous is None:
        return []
    return [{"source": "homeassistant", "kind": "state_change",
             "entity_id": entity_id, "state": state,
             "previous_state": previous[entity_id]}
            for entity_id, state in current.items()
            if entity_id in previous and previous[entity_id] != state]


def fire(rule: dict, dispatch, now: float | None = None) -> None:
    """Run one rule's actions as its identity, one policy check per action.

    `dispatch` is the gateway's own dispatch function, injected rather than
    imported so tests can watch it and so this module never imports server
    (which imports the world).
    """
    from integrations import _client

    stamp = time.time() if now is None else now
    _last_fired[str(rule.get("name"))] = stamp
    identity = str(rule.get("identity") or "")
    _client.CURRENT_IDENTITY.set(identity)
    for action in grammar.actions_for(rule):
        tool, args = action["tool"], action["args"]
        started = time.monotonic()

        def note(outcome: str, detail: str = "",
                 tool=tool, args=args, started=started) -> None:
            outcome_log.record(
                SERVICE, tool, outcome,
                capability=policy_gate.capability_of(tool),
                arguments=args, ms=time.monotonic() - started,
                detail=detail or f"rule:{rule.get('name')}", identity=identity)

        try:
            # The same gate a session's call passes, non-consuming for the
            # same reason: the bridge's own check is the authoritative one.
            policy_gate.check(tool, consume=False, identity=identity)
            dispatch(tool, args)
            note(outcome_log.OK)
        except policy_gate.PolicyDenied:
            # Recorded and dropped, never retried. A rule needing a grant
            # nobody issued does nothing, because 3am is when nobody is
            # reading approval prompts.
            note(outcome_log.DENIED)
        except Exception as exc:  # noqa: BLE001 (one bad action, not a dead loop)
            note(outcome_log.ERROR, detail=type(exc).__name__)


def evaluate_pass(events: list[dict], dispatch,
                  directory: Path = RULES_DIR,
                  approved_path: Path = APPROVED_PATH,
                  now: float | None = None) -> int:
    """Match every event against every active rule; fire what matches.

    Returns the number of rules fired, for the tick log line.
    """
    stamp = time.time() if now is None else now
    fired = 0
    for rule in active_rules(directory, approved_path):
        name = str(rule.get("name"))
        if stamp - _last_fired.get(name, 0.0) < COOLDOWN_SECONDS:
            continue
        for event in events:
            if grammar.matches(rule, event):
                fire(rule, dispatch, now=stamp)
                fired += 1
                break  # one firing per rule per pass, whatever else matched
    return fired


def _loop(dispatch) -> None:
    while True:
        try:
            events = [schedule_event()]
            try:
                events.extend(homeassistant_events())
            except Exception:  # noqa: BLE001 (a bridge outage is not fatal)
                pass
            fired = evaluate_pass(events, dispatch)
            if fired:
                print(f"{SERVICE}: fired {fired} rule(s)",
                      file=sys.stderr, flush=True)
        except Exception as exc:  # noqa: BLE001 (the loop must outlive anything)
            print(f"{SERVICE}: pass failed: {type(exc).__name__}",
                  file=sys.stderr, flush=True)
        time.sleep(INTERVAL_SECONDS)


def start(dispatch) -> bool:
    """Start the evaluator thread. Returns whether it started."""
    if not ENABLED:
        print(f"{SERVICE}: disabled (AGENTBOX_RULES_EVALUATOR=0)",
              file=sys.stderr, flush=True)
        return False
    thread = threading.Thread(target=_loop, args=(dispatch,),
                              name=SERVICE, daemon=True)
    thread.start()
    print(f"{SERVICE}: evaluating every {INTERVAL_SECONDS}s; live sources: "
          f"{', '.join(LIVE_SOURCES)}; cooldown {COOLDOWN_SECONDS}s",
          file=sys.stderr, flush=True)
    return True
