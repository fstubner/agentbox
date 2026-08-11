"""Proposing rules. The assistant authors; a human decides; code evaluates.

A stored rule runs unattended, forever, as somebody. That is a bigger thing to
create than a tool call, so it goes through the same shape as memory: the
assistant proposes, the proposal is inert, and an operator activates it.

Validation happens here, at authoring time, so the assistant learns
immediately that a rule is wrong — while it still has the context to fix it —
rather than a person discovering it months later when the rule quietly does
nothing.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import rules as grammar
from mcp_base import ToolError, schema_object

STORE = Path(os.environ.get("AGENTBOX_RULES_DIR", "/policy-state/rules"))

TOOLS = [
    {"name": "propose_rule",
     "description": "Propose a standing rule: when something happens, if some "
                    "conditions hold, do these tool calls. The rule is "
                    "checked immediately and stored inert — a person activates "
                    "it. Rules take literal values only; there are no "
                    "templates and nothing from the event is substituted.",
     "inputSchema": schema_object({
         "name": {"type": "string",
                  "description": "lowercase-with-hyphens, unique"},
         "description": {"type": "string",
                         "description": "why this rule exists, for the person "
                                        "deciding whether to activate it"},
         "when": {"type": "object",
                  "description": "trigger: source plus event fields to match"},
         "if": {"type": "array", "items": {"type": "object"},
                "description": "predicates comparing an event field to a "
                               "literal"},
         "do": {"type": "array", "items": {"type": "object"},
                "description": "tool calls, each {tool, args}"}},
         ["name", "when", "do"])},

    {"name": "list_rules",
     "description": "Standing rules and proposals, with whether each is active.",
     "inputSchema": schema_object({})},
]


def _identity():
    from integrations._client import CURRENT_IDENTITY
    return CURRENT_IDENTITY.get() or ""


def _known_tools():
    """The tools the assistant itself has.

    Imported lazily and read from the live registry, so a rule can never name
    something the assistant could not call directly — the rule surface is a
    subset of the tool surface by construction rather than by a second list
    somebody has to keep in step.
    """
    import server
    return {tool["name"]: tool for tool in server.TOOLS}


def _known_identities():
    import server
    return set(server.load_identities()) or {_identity()}


def dispatch(name, args):
    if name == "list_rules":
        # Active is derived from the operator's approval record on the
        # read-only mount, never from the rule file: the file lives on this
        # container's writable mount, and nothing written there may confer
        # authority — including the appearance of it in a listing.
        import evaluator
        approved = evaluator.approvals()
        found = []
        for path in sorted(STORE.glob("*.json")) if STORE.is_dir() else []:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            active = approved.get(str(record.get("name"))) == \
                evaluator.fingerprint(record)
            found.append({"name": record.get("name"),
                          "identity": record.get("identity"),
                          "active": active,
                          "description": record.get("description", "")})
        return {"rules": found, "total": len(found),
                "note": "inactive rules never fire; a person activates them "
                        "with `agentbox rules approve <name>`"}

    if name != "propose_rule":
        raise ToolError(f"unknown tool: {name}")

    identity = _identity()
    if not identity:
        raise ToolError("a rule must belong to an identity, and this session "
                        "has none")
    proposal = {**args, "identity": identity}
    try:
        checked = grammar.validate(proposal, _known_tools(),
                                   _known_identities())
    except grammar.RuleInvalid as exc:
        # Deliberately a tool error rather than a stored-but-broken rule: the
        # assistant can read this and try again now.
        raise ToolError(str(exc)) from None

    checked["proposed_at"] = int(time.time())
    try:
        STORE.mkdir(parents=True, exist_ok=True)
        path = STORE / f"{checked['name']}.json"
        # Checked against the operator's approval record, not a flag in the
        # file. Overwriting an approved rule would not grant anything — the
        # fingerprint pin means the replacement simply never fires — but it
        # would silently kill something a person chose to have running, which
        # is its own kind of harm.
        import evaluator
        if checked["name"] in evaluator.approvals():
            raise ToolError(
                f"'{checked['name']}' is active. Rules are not edited in "
                f"place — propose a differently named rule and ask for the "
                f"old one to be retired.")
        path.write_text(json.dumps(checked, indent=2), encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"could not store the rule: {type(exc).__name__}") from None

    import evaluator
    source = checked["when"].get("source", "")
    live = source in evaluator.LIVE_SOURCES
    result = {"stored": checked["name"], "active": False,
              "next": "A person must run `agentbox rules approve "
                      f"{checked['name']}` before this ever fires."}
    if not live:
        # Honest about the platform's own state, not just the rule's. Tell
        # the author now, while it can relay that to the person asking,
        # rather than letting both believe an approved rule is live.
        result["caveat"] = (
            f"'{source}' events are not wired up yet (live sources: "
            f"{', '.join(evaluator.LIVE_SOURCES)}). Even once approved, this "
            f"rule cannot fire until that source lands — say so if someone "
            f"is counting on it.")
    return result
