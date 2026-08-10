"""The rules grammar.

The whole argument for giving up expressiveness is that a rule which passes
validation cannot fail at fire time for a reason somebody has to debug at 3am.
These test that claim: everything wrong is caught at authoring time, and
evaluation is total.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "services" / "compose" / "agentbox-mcp" / "app"


@pytest.fixture
def r():
    spec = importlib.util.spec_from_file_location("rules", APP / "rules.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["rules"] = module
    spec.loader.exec_module(module)
    return module


TOOLS = {
    "speak_aloud": {"inputSchema": {"properties": {"text": {}, "speaker": {}},
                                    "required": ["text"]}},
    "set_home_light": {"inputSchema": {"properties": {"entity_id": {},
                                                      "state": {}},
                                       "required": ["entity_id", "state"]}},
}
WHO = {"alex", "sam"}


def good():
    return {
        "name": "washing-done",
        "identity": "alex",
        "when": {"source": "homeassistant", "entity_id": "switch.washer",
                 "state": "off"},
        "if": [{"field": "previous_state", "equals": "on"}],
        "do": [{"tool": "speak_aloud",
                "args": {"text": "The washing machine has finished.",
                         "speaker": "kitchen"}}],
    }


# --- static checking ------------------------------------------------------------


def test_a_well_formed_rule_validates(r):
    out = r.validate(good(), TOOLS, WHO)
    assert out["name"] == "washing-done"
    assert out["identity"] == "alex"
    assert out["enabled"] is True


def test_templates_are_not_part_of_the_grammar(r):
    """Not filtered — absent. There is nothing to escape because there is no
    syntax for substitution."""
    for bad in ("{{ state }}", "${state}", "<% state %>"):
        rule = good()
        rule["do"][0]["args"]["text"] = f"washer is {bad}"
        with pytest.raises(r.RuleInvalid) as exc:
            r.validate(rule, TOOLS, WHO)
        assert "template" in str(exc.value).lower()


def test_templates_are_refused_at_any_depth(r):
    rule = good()
    rule["when"]["entity_id"] = "switch.{{ evil }}"
    with pytest.raises(r.RuleInvalid):
        r.validate(rule, TOOLS, WHO)


def test_a_rule_must_belong_to_a_real_identity(r):
    """A rule fires as somebody — their credentials, their scope. An unowned
    rule is an unattended action with nobody accountable for it."""
    rule = good()
    rule["identity"] = "nobody"
    with pytest.raises(r.RuleInvalid) as exc:
        r.validate(rule, TOOLS, WHO)
    assert "not configured" in str(exc.value)


def test_a_rule_cannot_call_a_tool_the_assistant_lacks(r):
    rule = good()
    rule["do"] = [{"tool": "delete_everything", "args": {}}]
    with pytest.raises(r.RuleInvalid) as exc:
        r.validate(rule, TOOLS, WHO)
    assert "not a tool" in str(exc.value)


def test_missing_required_arguments_are_caught_at_authoring_time(r):
    """The failure this replaces: a rule that stores fine and does nothing at
    3am because a required field was never there."""
    rule = good()
    rule["do"] = [{"tool": "set_home_light", "args": {"entity_id": "light.x"}}]
    with pytest.raises(r.RuleInvalid) as exc:
        r.validate(rule, TOOLS, WHO)
    assert "requires" in str(exc.value)


def test_unknown_arguments_are_refused(r):
    rule = good()
    rule["do"][0]["args"]["volume"] = 11
    with pytest.raises(r.RuleInvalid) as exc:
        r.validate(rule, TOOLS, WHO)
    assert "no argument" in str(exc.value)


def test_a_predicate_over_an_unknown_field_is_refused(r):
    """It would never match and never say why — the worst way to be wrong."""
    rule = good()
    rule["if"] = [{"field": "moon_phase", "equals": "full"}]
    with pytest.raises(r.RuleInvalid) as exc:
        r.validate(rule, TOOLS, WHO)
    assert "not an event field" in str(exc.value)


def test_a_predicate_needs_exactly_one_comparison(r):
    rule = good()
    rule["if"] = [{"field": "state", "equals": "on", "not_equals": "off"}]
    with pytest.raises(r.RuleInvalid):
        r.validate(rule, TOOLS, WHO)


def test_unknown_trigger_sources_are_refused(r):
    rule = good()
    rule["when"]["source"] = "the_internet"
    with pytest.raises(r.RuleInvalid):
        r.validate(rule, TOOLS, WHO)


def test_a_rule_is_bounded(r):
    rule = good()
    rule["do"] = [dict(rule["do"][0]) for _ in range(r.MAX_ACTIONS + 1)]
    with pytest.raises(r.RuleInvalid) as exc:
        r.validate(rule, TOOLS, WHO)
    assert "not a program" in str(exc.value)


def test_capability_is_surfaced_to_the_author(r):
    """So they learn a rule needs a grant now, rather than finding out when it
    silently does nothing."""
    out = r.validate(good(), TOOLS, WHO, capability_of=lambda t: "speak_aloud")
    assert out["do"][0]["_capability"] == "speak_aloud"


# --- evaluation -----------------------------------------------------------------


def test_a_matching_event_fires(r):
    rule = r.validate(good(), TOOLS, WHO)
    assert r.matches(rule, {"source": "homeassistant",
                            "entity_id": "switch.washer",
                            "state": "off", "previous_state": "on"}) is True


def test_a_near_miss_does_not_fire(r):
    rule = r.validate(good(), TOOLS, WHO)
    for event in (
        {"source": "homeassistant", "entity_id": "switch.other",
         "state": "off", "previous_state": "on"},
        {"source": "gmail", "entity_id": "switch.washer",
         "state": "off", "previous_state": "on"},
        {"source": "homeassistant", "entity_id": "switch.washer",
         "state": "off", "previous_state": "off"},
    ):
        assert r.matches(rule, event) is False


def test_a_disabled_rule_never_fires(r):
    rule = r.validate({**good(), "enabled": False}, TOOLS, WHO)
    assert r.matches(rule, {"source": "homeassistant",
                            "entity_id": "switch.washer",
                            "state": "off", "previous_state": "on"}) is False


def test_evaluation_is_total(r):
    """No exception escapes. A rule that cannot be evaluated does not fire,
    which is the safe direction for something running unattended."""
    rule = r.validate(good(), TOOLS, WHO)
    for event in ({}, {"source": None}, {"previous_state": object()},
                  {"source": "homeassistant", "entity_id": "switch.washer",
                   "state": "off", "previous_state": None}):
        assert r.matches(rule, event) in (True, False)


def test_comparisons_do_not_coerce_surprisingly(r):
    assert r.OPERATORS["greater_than"]("10", "9") is True
    assert r.OPERATORS["greater_than"]("abc", "9") is False
    # A bool is not a number here; True > 0 would be a nasty way to fire.
    assert r.OPERATORS["greater_than"](True, 0) is False


def test_actions_are_taken_literally(r):
    """Nothing from the event is interpolated, because the grammar cannot say
    that. A rule does the same thing every time, so reading it is enough."""
    rule = r.validate(good(), TOOLS, WHO)
    actions = r.actions_for(rule)
    assert actions == [{"tool": "speak_aloud",
                        "args": {"text": "The washing machine has finished.",
                                 "speaker": "kitchen"}}]
    actions[0]["args"]["text"] = "mutated"
    assert r.actions_for(rule)[0]["args"]["text"].startswith("The washing")


def test_the_grammar_is_shipped_in_the_gateway_image():
    """It was not, first time: the container crash-looped on ModuleNotFoundError
    because a new top-level module was written but never COPYed."""
    dockerfile = (REPO / "services/compose/agentbox-mcp/Dockerfile").read_text()
    assert "app/rules.py" in dockerfile
