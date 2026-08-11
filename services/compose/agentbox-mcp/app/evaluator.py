"""The rules evaluator: the half of item 11 that makes rules actually fire.

The grammar (`rules.py`) authored and validated rules; nothing fed them
events. An approved rule that silently never fires is the confident-but-wrong
state this platform keeps hunting down, so from here on `rules approve` means
what it says — for the sources this file feeds.

## What feeds it

Two sources are live, chosen because both are observable from inside the
gateway without new credentials:

- **schedule** — one tick per evaluation pass: `{"source": "schedule",
  "kind": "tick", "at": "HH:MM"}`. A rule matching `at: "07:30"` fires once
  that minute (the cooldown, below, is what makes "once" true).
- **homeassistant** — entity state diffs, polled through the same bridge and
  lean view the assistant uses: `{"source": "homeassistant", "kind":
  "state_change", "entity_id", "state", "previous_state"}`. The first poll
  seeds a baseline and emits nothing, so a gateway restart cannot replay the
  whole house as fresh events.

gmail, calendar and vikunja remain valid grammar and dead sources; `rules
approve` names which is which so nobody believes a mail rule is live.

## What firing means

Exactly what the grammar promised: each `do` is an ordinary tool call. The
action passes the same `policy_gate.check` the assistant's own calls pass
(non-consuming, the bridge's consume stays authoritative), runs as the rule's
identity via the same contextvar the gateway sets for a session, and lands in
the same outcome journal — under `service: "agentbox-rules"` with the rule's
name in `detail`, so reflection can tell a rule's actions from the
assistant's. A denial is recorded, not retried: a rule needing a grant at 3am
does nothing, which is the safe direction.

## Where approval lives, and why it is not a flag in the rule file

Rule files sit in `/policy-state`, the container-writable mount whose whole
design contract is that writing there confers no authority. An `active` flag
inside the rule file would break that contract in the worst place: a
compromised gateway could approve its own rule — or quietly rewrite an
approved rule's `do` list — and gain unattended execution, forever, as
somebody. So approval is an entry in `/policy/rules-approved.json`, which is
the operator-owned mount the container reads and cannot write (the same split
as grants), and the entry pins a fingerprint of the rule's executing content.
A rule fires only while the stored file still hashes to what the operator
approved: editing an approved rule voids its approval rather than inheriting
it.

## Restraints

- **Only operator-approved, unmodified rules fire** (above). `matches()` is
  never the arbiter of that: its `enabled` field is true on every unapproved
  proposal from the moment it is written.
- **One firing per rule per COOLDOWN_SECONDS** (default 300). This is what
  turns "the 07:30 tick matched twice because the loop runs twice a minute"
  and "a flapping sensor" into one action, not a stream.
- **No exception escapes.** A broken rule, an unreachable bridge, or a failed
  action is logged and skipped; the loop and the gateway outlive all of them.

In-memory state (baseline, cooldowns) resets on restart. Worst case: a rule
re-fires up to one cooldown early after a redeploy. Accepted — persisting
fire-state would add a writable file for a property nobody has needed yet.
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


# What the operator's approval pins. Exactly the fields that execute and say
# as whom — not description or timestamps, which are display. cli/agentbox
# reimplements this (it cannot import container modules); the parity test in
# tests/test_rules_evaluator.py is what keeps the two identical.
FINGERPRINT_FIELDS = ("name", "identity", "when", "if", "do")


def fingerprint(record: dict) -> str:
    core = {key: record.get(key) for key in FINGERPRINT_FIELDS}
    return hashlib.sha256(json.dumps(
        core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def approvals(path: Path = APPROVED_PATH) -> dict[str, str]:
    """name -> approved fingerprint. Unreadable means nothing is approved —
    the same failure direction as an unreadable grants file."""
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
    """Stored rules the operator has approved, still reading as approved.

    Authority comes from the approvals file on the read-only mount, never
    from anything inside the rule record — the record lives on the writable
    mount, and a flag there would let this container approve its own rules.
    The fingerprint comparison is what makes editing an approved rule void
    its approval instead of inheriting it.
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
            # Recorded and dropped, never retried. A rule that needs a grant
            # nobody has issued does nothing — 3am is exactly when no human is
            # reading approval prompts, which is the argument the whole
            # policy design is built on.
            note(outcome_log.DENIED)
        except Exception as exc:  # noqa: BLE001 — one bad action, not a dead loop
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
            except Exception:  # noqa: BLE001 — a bridge outage is not fatal
                pass
            fired = evaluate_pass(events, dispatch)
            if fired:
                print(f"{SERVICE}: fired {fired} rule(s)",
                      file=sys.stderr, flush=True)
        except Exception as exc:  # noqa: BLE001 — the loop must outlive anything
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
