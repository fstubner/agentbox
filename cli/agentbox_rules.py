"""Listing, approving and removing standing rules.

Approvals are written to the operator-owned policy mount and pin a fingerprint
of each rule's executing fields, so an edited rule stops firing.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from agentbox_common import FAIL, OK, WARN, report

RULES_DIR = Path(os.environ.get(
    "AGENTBOX_RULES_DIR",
    os.path.expanduser("~/.local/state/agentbox/policy-state/rules")))

RULES_APPROVED_PATH = Path(os.environ.get(
    "AGENTBOX_RULES_APPROVED",
    os.path.expanduser("~/.local/state/agentbox/policy/rules-approved.json")))

# What an approval pins: the fields that execute and say as whom. Must stay
# identical to evaluator.FINGERPRINT_FIELDS — tests/test_rules_evaluator.py
# computes both over the same rule and fails on drift.
RULE_FINGERPRINT_FIELDS = ("name", "identity", "when", "if", "do")


def rule_fingerprint(record: dict) -> str:
    core = {key: record.get(key) for key in RULE_FINGERPRINT_FIELDS}
    return hashlib.sha256(json.dumps(
        core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _rule_approvals(approved_path: Path | None = None) -> dict[str, str]:
    path = approved_path or RULES_APPROVED_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(e.get("name", "")): str(e.get("sha", ""))
            for e in (data.get("approved", []) if isinstance(data, dict) else [])
            if e.get("name") and e.get("sha")}


def _write_rule_approvals(entries: dict[str, str], approved_path: Path | None = None) -> None:
    path = approved_path or RULES_APPROVED_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"approved": [{"name": name, "sha": sha}
                      for name, sha in sorted(entries.items())]},
        indent=2), encoding="utf-8")


def _rules(rules_dir: Path | None = None, approved_path: Path | None = None) -> list[dict]:
    rdir = rules_dir or RULES_DIR
    out = []
    approved = _rule_approvals(approved_path)
    if not rdir.is_dir():
        return out
    for path in sorted(rdir.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        record["_path"] = str(path)
        # Derived from the operator's approval record, never from the file:
        # the file sits on the gateway-writable mount and carries no authority.
        sha = approved.get(str(record.get("name")))
        record["_active"] = sha == rule_fingerprint(record)
        record["_edited_since_approval"] = bool(sha) and not record["_active"]
        out.append(record)
    return out


def rules_list(rules_dir: Path | None = None, approved_path: Path | None = None) -> int:
    """Show standing rules and what each one actually does.

    Printed in full rather than summarised. A rule is the assistant's only way
    to cause something to happen when nobody is watching, so the person
    deciding needs to read the whole thing, not a description of it.
    """
    found = _rules(rules_dir, approved_path)
    if not found:
        print("no rules")
        return 0
    for record in found:
        state = "ACTIVE " if record.get("_active") else "inactive"
        print(f"[{state}] {record.get('name')}  (as {record.get('identity')})")
        if record.get("_edited_since_approval"):
            print("          !! changed since it was approved — the approval "
                  "is void and it will not fire. Re-read it and approve again.")
        if record.get("description"):
            print(f"          {record['description']}")
        when = record.get("when", {})
        print(f"          when: {when}")
        for predicate in record.get("if") or []:
            print(f"          if:   {predicate}")
        for action in record.get("do") or []:
            print(f"          do:   {action.get('tool')} {action.get('args')}")
        print()
    print("activate with: cli/agentbox rules approve <name>")
    return 0


def rules_decide(name: str, activate: bool,
                 rules_dir: Path | None = None,
                 rules_approved_path: Path | None = None) -> int:
    """Approve or deactivate one rule.

    Approval is an entry in the operator-owned approvals file, pinning a
    fingerprint of what the rule executes. Nothing is written to the rule
    file itself — that sits on the gateway-writable mount, and an approval
    the approved party could write would not be one. Pinning means an
    approved rule that is later edited (by anything) simply stops firing.
    """
    for record in _rules(rules_dir, rules_approved_path):
        if record.get("name") != name:
            continue
        entries = _rule_approvals(rules_approved_path)
        if activate:
            entries[name] = rule_fingerprint(record)
        else:
            entries.pop(name, None)
        try:
            _write_rule_approvals(entries, rules_approved_path)
        except OSError as exc:
            target = rules_approved_path or RULES_APPROVED_PATH
            report(FAIL, f"could not write {target}: {exc}")
            return 1
        report(OK, f"{'activated' if activate else 'deactivated'} rule "
                            f"'{name}'")
        if activate:
            report(WARN, "this rule now runs unattended, as "
                                  f"{record.get('identity')}, until deactivated. If "
                                  f"the rule file changes in any way, the approval is "
                                  f"void and it stops firing.")
            # Say so at the moment of approval, not in a document. An approved
            # rule that silently never fires is exactly the confident-but-wrong
            # state this system keeps having to hunt down. Kept in step with
            # evaluator.LIVE_SOURCES by tests/test_rules_evaluator.py.
            live = ("schedule", "homeassistant")
            source = str((record.get("when") or {}).get("source", ""))
            if source in live:
                report(OK, f"'{source}' events are live — this rule can fire "
                                    f"within a minute or two of matching")
            else:
                report(WARN, f"'{source}' events are NOT wired up yet (live: "
                                      f"{', '.join(live)}), so this rule is recorded "
                                      f"but cannot fire until that source lands")
        return 0
    report(FAIL, f"no rule named '{name}'")
    return 1


def rules_remove(name: str,
                 rules_dir: Path | None = None,
                 rules_approved_path: Path | None = None) -> int:
    for record in _rules(rules_dir, rules_approved_path):
        if record.get("name") == name:
            # The approval is what stops it firing, so revoke that first and
            # unconditionally — file deletion can fail (the rules dir is
            # container-owned) and must not leave the rule live.
            entries = _rule_approvals(rules_approved_path)
            if entries.pop(name, None) is not None:
                _write_rule_approvals(entries, rules_approved_path)
            try:
                Path(record["_path"]).unlink()
                report(OK, f"removed rule '{name}'")
            except OSError:
                report(OK, f"revoked approval for '{name}'; it cannot fire")
                report(WARN, f"the proposal file remains at "
                                      f"{record['_path']} (owned by the gateway) — "
                                      f"harmless, and listed as inactive")
            return 0
    report(FAIL, f"no rule named '{name}'")
    return 1
