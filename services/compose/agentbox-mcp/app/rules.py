"""Cross-service rules: a designed grammar, deliberately not a language.

    rule = when <bridge-observed event | schedule>
           if   <literal predicates — no templates, no code>
           do   <allowlisted calls to tools the assistant already has>

## Why a grammar rather than n8n

n8n was evaluated and rejected — not because it could not be locked down, but
because locking it down is the *subtractive* shape: renting a general-purpose
surface and re-auditing the subtraction on every upgrade. This is the additive
shape. Expressiveness is a budget, spent only on what we are willing to verify.

## Why it is not Turing-complete, on purpose

Every rule is statically checkable before it is ever stored: the tool exists,
the arguments satisfy its schema, the predicates compare literals, the identity
is real. A rule that passes validation cannot fail at fire time for a reason
anybody has to debug at 3am — which is the whole argument for giving up
expressiveness.

There are no templates. Not "templates are filtered" — the grammar has no
syntax for them, so there is nothing to escape and nothing to get wrong. Any
`{{` anywhere in a rule is a validation error rather than a substitution.

## The model authors; it does not evaluate

The assistant proposes rules. Evaluation is deterministic code in this file.
That split is what keeps a compromised model from turning a stored rule into
an arbitrary action: it can propose something wrong, and a human reads it,
and then it runs exactly as written forever.

## Every `do` is an ordinary tool call

No new execution machinery. The policy gate, grants, identity binding and the
outcome journal all apply unchanged, and the daily reflection sees what rules
did alongside what the assistant did.
"""
from __future__ import annotations

import re
from typing import Any

# Predicates compare a field to a literal. That is the entire vocabulary.
#
# No arithmetic, no string building, no regular expressions, no references to
# other rules. Each one is a total function over two values, so evaluation
# cannot loop, throw, or depend on anything outside the event.
OPERATORS = {
    "equals": lambda a, b: a == b,
    "not_equals": lambda a, b: a != b,
    "is_one_of": lambda a, b: isinstance(b, list) and a in b,
    "is_not_one_of": lambda a, b: isinstance(b, list) and a not in b,
    "greater_than": lambda a, b: _number(a) is not None and _number(b) is not None
    and _number(a) > _number(b),
    "less_than": lambda a, b: _number(a) is not None and _number(b) is not None
    and _number(a) < _number(b),
    "contains": lambda a, b: isinstance(a, str) and isinstance(b, str) and b in a,
    "is_present": lambda a, b: (a is not None) == bool(b),
}

# Event sources a rule may trigger on. A closed set: an event kind nobody has
# thought about is not a trigger, it is a validation error.
EVENT_SOURCES = ("homeassistant", "gmail", "calendar", "vikunja", "schedule")

# Fields an event may expose. Closed for the same reason as the operators: a
# predicate over an unknown field silently never matches, which is the worst
# way for a rule to be wrong.
EVENT_FIELDS = ("source", "kind", "entity_id", "state", "previous_state",
                "label", "subject", "sender", "project", "title", "at")

MAX_ACTIONS = 5          # a rule is a rule, not a program
MAX_PREDICATES = 10
TEMPLATE = re.compile(r"\{\{|\}\}|\$\{|<%")


class RuleInvalid(Exception):
    """Raised at authoring time, never at fire time.

    The message reaches whoever proposed the rule — usually the assistant — so
    it says what to change rather than what went wrong internally.
    """


def _number(value: Any):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _no_templates(node: Any, where: str) -> None:
    """Refuse template syntax anywhere in the rule, at any depth.

    A rule is data. The moment any part of it is evaluated as an expression,
    static checking stops meaning anything and the grammar has quietly become
    a language.
    """
    if isinstance(node, str):
        if TEMPLATE.search(node):
            raise RuleInvalid(
                f"{where}: templates are not part of this grammar. Rules "
                f"compare and pass literal values; there is nothing to "
                f"substitute.")
    elif isinstance(node, dict):
        for key, value in node.items():
            _no_templates(key, where)
            _no_templates(value, f"{where}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _no_templates(value, f"{where}[{index}]")


def validate(rule: dict, known_tools: dict[str, dict],
             known_identities: set[str],
             capability_of=None) -> dict:
    """Check a rule completely, before it is ever stored.

    Returns a normalised copy. Raises RuleInvalid with a message for the
    author. Every check here is one that would otherwise be a surprise at fire
    time, and fire time is unattended.
    """
    if not isinstance(rule, dict):
        raise RuleInvalid("a rule must be an object")
    _no_templates(rule, "rule")

    name = str(rule.get("name", "")).strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,48}", name):
        raise RuleInvalid("name must be lowercase letters, digits and hyphens, "
                          "3-49 characters")

    # A rule fires as somebody. Not as "the system": every action it takes uses
    # that person's credentials and is visible in their scope, so an unowned
    # rule would be an action with no accountable identity behind it.
    identity = str(rule.get("identity", "")).strip()
    if identity not in known_identities:
        raise RuleInvalid(f"identity '{identity}' is not configured; known: "
                          f"{sorted(known_identities)}")

    when = rule.get("when")
    if not isinstance(when, dict):
        raise RuleInvalid("`when` must be an object describing the trigger")
    source = str(when.get("source", "")).strip()
    if source not in EVENT_SOURCES:
        raise RuleInvalid(f"unknown trigger source '{source}'; allowed: "
                          f"{list(EVENT_SOURCES)}")
    for key in when:
        if key not in ("source", *EVENT_FIELDS):
            raise RuleInvalid(f"`when.{key}` is not a known event field; "
                              f"allowed: {list(EVENT_FIELDS)}")

    predicates = rule.get("if") or []
    if not isinstance(predicates, list):
        raise RuleInvalid("`if` must be a list of predicates")
    if len(predicates) > MAX_PREDICATES:
        raise RuleInvalid(f"at most {MAX_PREDICATES} predicates")
    for index, predicate in enumerate(predicates):
        if not isinstance(predicate, dict):
            raise RuleInvalid(f"if[{index}] must be an object")
        field = str(predicate.get("field", ""))
        if field not in EVENT_FIELDS:
            raise RuleInvalid(
                f"if[{index}]: '{field}' is not an event field. A predicate "
                f"over an unknown field would never match and never say why.")
        operators = [k for k in predicate if k in OPERATORS]
        if len(operators) != 1:
            raise RuleInvalid(
                f"if[{index}]: exactly one comparison per predicate, from "
                f"{sorted(OPERATORS)}")
        for key in predicate:
            if key != "field" and key not in OPERATORS:
                raise RuleInvalid(f"if[{index}]: unknown key '{key}'")

    actions = rule.get("do") or []
    if not isinstance(actions, list) or not actions:
        raise RuleInvalid("`do` must be a non-empty list of tool calls")
    if len(actions) > MAX_ACTIONS:
        raise RuleInvalid(f"at most {MAX_ACTIONS} actions; a rule is a rule, "
                          f"not a program")
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise RuleInvalid(f"do[{index}] must be an object")
        tool = str(action.get("tool", ""))
        if tool not in known_tools:
            raise RuleInvalid(
                f"do[{index}]: '{tool}' is not a tool the assistant has. A "
                f"rule cannot reach anything the assistant could not call "
                f"itself.")
        args = action.get("args") or {}
        if not isinstance(args, dict):
            raise RuleInvalid(f"do[{index}].args must be an object")
        schema = known_tools[tool].get("inputSchema") or {}
        required = schema.get("required") or []
        missing = [field for field in required if field not in args]
        if missing:
            raise RuleInvalid(f"do[{index}]: '{tool}' requires {missing}")
        allowed = set(schema.get("properties") or {})
        unknown = sorted(set(args) - allowed)
        if allowed and unknown:
            raise RuleInvalid(f"do[{index}]: '{tool}' has no argument(s) "
                              f"{unknown}")
        # Recorded, not enforced here: the policy gate is authoritative at fire
        # time. Surfacing it now means the author learns that a rule will need
        # a grant before it silently does nothing at 3am.
        if capability_of is not None:
            action["_capability"] = capability_of(tool) or ""

    return {"name": name, "identity": identity, "when": dict(when),
            "if": [dict(p) for p in predicates],
            "do": [dict(a) for a in actions],
            "enabled": bool(rule.get("enabled", True)),
            "description": str(rule.get("description", ""))[:300]}


def matches(rule: dict, event: dict) -> bool:
    """Whether this event fires this rule. Deterministic and total.

    No exception escapes: a rule that cannot be evaluated does not fire, and a
    rule that does not fire is the safe direction for an unattended action.
    """
    if not rule.get("enabled", True):
        return False
    when = rule.get("when") or {}
    for key, expected in when.items():
        if event.get(key) != expected:
            return False
    for predicate in rule.get("if") or []:
        field = predicate.get("field")
        operator = next((k for k in predicate if k in OPERATORS), None)
        if operator is None:
            return False
        try:
            if not OPERATORS[operator](event.get(field), predicate[operator]):
                return False
        except Exception:  # noqa: BLE001 — a broken predicate must not fire
            return False
    return True


def actions_for(rule: dict) -> list[dict]:
    """The calls to make, exactly as written.

    Nothing from the event is interpolated, because the grammar has no way to
    say that. A rule does the same thing every time it fires, which is what
    makes reading one enough to know what it does.
    """
    return [{"tool": a["tool"], "args": dict(a.get("args") or {})}
            for a in rule.get("do") or []]
