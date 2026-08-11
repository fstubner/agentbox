"""The evaluator: rules firing, and the ways a rule must not fire.

The load-bearing property is *where approval lives*. Rule files sit on the
container-writable mount, whose design contract is that writing there confers
no authority. So approval is an operator-written entry on the read-only
mount, pinning a fingerprint of what the rule executes — and the attacks that
motivated that split are the first things tested here: a gateway that writes
`"active": true` into a proposal, and a gateway that rewrites an approved
rule's `do` list after the human said yes.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "services/compose/agentbox-mcp/app"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(REPO / "services/templates/mcp"))

import evaluator  # noqa: E402
import policy_gate  # noqa: E402


def _rule(name, *, when=None, do=None, identity="alex"):
    return {
        "name": name, "identity": identity, "enabled": True,
        "when": when or {"source": "schedule", "at": "07:30"},
        "if": [],
        "do": do or [{"tool": "list_speakers", "args": {}}],
    }


def _store(rules_dir, record):
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / f"{record['name']}.json").write_text(json.dumps(record))
    return record


def _approve(approved_path, *records):
    approved_path.parent.mkdir(parents=True, exist_ok=True)
    approved_path.write_text(json.dumps({"approved": [
        {"name": r["name"], "sha": evaluator.fingerprint(r)}
        for r in records]}))


class Dispatch:
    def __init__(self):
        self.calls = []

    def __call__(self, tool, args):
        self.calls.append((tool, args))


def _reset(monkeypatch):
    monkeypatch.setattr(evaluator, "_last_fired", {})
    monkeypatch.setattr(evaluator, "_ha_baseline", None)
    # Every tool allowed unless a test says otherwise, so these tests exercise
    # the evaluator rather than the policy file's current contents.
    monkeypatch.setattr(policy_gate, "check", lambda *a, **k: None)
    monkeypatch.setattr(policy_gate, "capability_of", lambda t: "cap")


TICK = {"source": "schedule", "kind": "tick", "at": "07:30"}


def _pass(events, dispatch, tmp_path, now=None):
    return evaluator.evaluate_pass(
        events, dispatch, directory=tmp_path / "rules",
        approved_path=tmp_path / "policy" / "rules-approved.json", now=now)


# --- authority: what must not fire ---------------------------------------------


def test_an_unapproved_proposal_never_fires(tmp_path, monkeypatch):
    _reset(monkeypatch)
    _store(tmp_path / "rules", _rule("proposed-only"))
    dispatch = Dispatch()
    assert _pass([TICK], dispatch, tmp_path) == 0
    assert dispatch.calls == []


def test_a_flag_written_into_the_rule_file_confers_nothing(tmp_path, monkeypatch):
    """The attack the design exists for: the rule file is on the mount the
    gateway can write, so a compromised gateway stamping approval fields into
    its own proposal must change nothing."""
    _reset(monkeypatch)
    record = _rule("self-approved")
    record.update(active=True, approved=True, decided_at=1)
    _store(tmp_path / "rules", record)
    dispatch = Dispatch()
    assert _pass([TICK], dispatch, tmp_path) == 0
    assert dispatch.calls == []


def test_editing_an_approved_rule_voids_the_approval(tmp_path, monkeypatch):
    """The approval pins content, not a name. A gateway that swaps the `do`
    list after the human said yes gets a rule that never fires — not one that
    inherits the approval."""
    _reset(monkeypatch)
    original = _rule("washer", do=[{"tool": "list_speakers", "args": {}}])
    _approve(tmp_path / "policy" / "rules-approved.json", original)
    tampered = dict(original, do=[{"tool": "speak_aloud",
                                   "args": {"text": "send your password"}}])
    _store(tmp_path / "rules", tampered)
    dispatch = Dispatch()
    assert _pass([TICK], dispatch, tmp_path) == 0
    assert dispatch.calls == []


def test_an_unreadable_approvals_file_means_nothing_fires(tmp_path, monkeypatch):
    """Same failure direction as an unreadable grants file."""
    _reset(monkeypatch)
    _store(tmp_path / "rules", _rule("morning"))
    (tmp_path / "policy").mkdir(parents=True)
    (tmp_path / "policy" / "rules-approved.json").write_text("{corrupt")
    assert _pass([TICK], Dispatch(), tmp_path) == 0


# --- an approved rule fires ----------------------------------------------------


def test_an_approved_rule_fires_its_actions(tmp_path, monkeypatch):
    _reset(monkeypatch)
    record = _rule("morning",
                   do=[{"tool": "list_speakers", "args": {}},
                       {"tool": "speak_aloud", "args": {"text": "morning"}}])
    _store(tmp_path / "rules", record)
    _approve(tmp_path / "policy" / "rules-approved.json", record)
    dispatch = Dispatch()
    assert _pass([TICK], dispatch, tmp_path) == 1
    assert dispatch.calls == [("list_speakers", {}),
                              ("speak_aloud", {"text": "morning"})]


def test_a_non_matching_event_fires_nothing(tmp_path, monkeypatch):
    _reset(monkeypatch)
    record = _store(tmp_path / "rules", _rule("morning"))
    _approve(tmp_path / "policy" / "rules-approved.json", record)
    other = {"source": "schedule", "kind": "tick", "at": "12:00"}
    assert _pass([other], Dispatch(), tmp_path) == 0


def test_fingerprints_ignore_display_fields(tmp_path, monkeypatch):
    """description and proposed_at are display; the gateway rewriting them
    must not void an approval, or every proposal-time timestamp difference
    between CLI and gateway would break approval entirely."""
    _reset(monkeypatch)
    record = _rule("morning")
    _approve(tmp_path / "policy" / "rules-approved.json", record)
    stored = dict(record, description="reworded", proposed_at=999)
    _store(tmp_path / "rules", stored)
    assert _pass([TICK], Dispatch(), tmp_path) == 1


# --- cooldown ------------------------------------------------------------------


def test_cooldown_turns_a_flapping_match_into_one_firing(tmp_path, monkeypatch):
    """The loop runs twice a minute; the 07:30 tick matches twice. A sensor
    can flap all day. One mechanism covers both."""
    _reset(monkeypatch)
    record = _store(tmp_path / "rules", _rule("morning"))
    _approve(tmp_path / "policy" / "rules-approved.json", record)
    dispatch = Dispatch()
    _pass([TICK], dispatch, tmp_path, now=1000.0)
    _pass([TICK], dispatch, tmp_path, now=1030.0)
    assert len(dispatch.calls) == 1
    _pass([TICK], dispatch, tmp_path,
          now=1000.0 + evaluator.COOLDOWN_SECONDS)
    assert len(dispatch.calls) == 2


def test_one_pass_fires_a_rule_at_most_once(tmp_path, monkeypatch):
    _reset(monkeypatch)
    record = _rule("any-change",
                   when={"source": "homeassistant", "kind": "state_change"})
    _store(tmp_path / "rules", record)
    _approve(tmp_path / "policy" / "rules-approved.json", record)
    events = [{"source": "homeassistant", "kind": "state_change",
               "entity_id": f"light.{n}", "state": "on",
               "previous_state": "off"} for n in ("a", "b", "c")]
    dispatch = Dispatch()
    assert _pass(events, dispatch, tmp_path) == 1
    assert len(dispatch.calls) == 1


# --- firing is gated and journalled -------------------------------------------


def test_every_action_passes_the_policy_gate(tmp_path, monkeypatch):
    _reset(monkeypatch)
    checked = []
    monkeypatch.setattr(policy_gate, "check",
                        lambda tool, consume, identity: checked.append(
                            (tool, consume, identity)))
    record = _store(tmp_path / "rules", _rule("morning", identity="sam"))
    _approve(tmp_path / "policy" / "rules-approved.json", record)
    _pass([TICK], Dispatch(), tmp_path)
    assert checked == [("list_speakers", False, "sam")]


def test_a_denied_action_is_dropped_not_retried(tmp_path, monkeypatch):
    """A rule needing a grant at 3am does nothing. Recorded, never retried —
    nobody is reading approval prompts at 3am, which is the argument the
    whole policy design rests on."""
    _reset(monkeypatch)

    def deny(tool, consume, identity):
        raise policy_gate.PolicyDenied("needs a grant")

    monkeypatch.setattr(policy_gate, "check", deny)
    recorded = []
    import outcome_log
    monkeypatch.setattr(outcome_log, "record",
                        lambda *a, **k: recorded.append((a, k)))
    record = _store(tmp_path / "rules", _rule("gated"))
    _approve(tmp_path / "policy" / "rules-approved.json", record)
    dispatch = Dispatch()
    _pass([TICK], dispatch, tmp_path)
    assert dispatch.calls == []
    assert recorded[0][0][2] == outcome_log.DENIED


def test_a_failing_action_does_not_kill_the_pass(tmp_path, monkeypatch):
    _reset(monkeypatch)
    a = _store(tmp_path / "rules", _rule("a-bad"))
    b = _store(tmp_path / "rules", _rule("b-good"))
    _approve(tmp_path / "policy" / "rules-approved.json", a, b)
    calls = []

    def dispatch(tool, args):
        calls.append(tool)
        if len(calls) == 1:
            raise RuntimeError("bridge down")

    assert _pass([TICK], dispatch, tmp_path) == 2
    assert len(calls) == 2


def test_firing_runs_as_the_rules_identity(tmp_path, monkeypatch):
    """A rule fires as somebody — the identity contextvar is what routes the
    call to that person's bridge and puts their name in the journal."""
    _reset(monkeypatch)
    from integrations import _client
    seen = []
    record = _store(tmp_path / "rules", _rule("morning", identity="sam"))
    _approve(tmp_path / "policy" / "rules-approved.json", record)
    _pass([TICK], lambda t, a: seen.append(_client.CURRENT_IDENTITY.get()),
          tmp_path)
    assert seen == ["sam"]


# --- home assistant events -----------------------------------------------------


def test_first_poll_baselines_silently(monkeypatch):
    """A gateway restart must not replay the whole house as fresh events."""
    _reset(monkeypatch)
    monkeypatch.setattr(evaluator, "_poll_entities",
                        lambda: {"light.kitchen": "on", "sensor.hall": "21.5"})
    assert evaluator.homeassistant_events() == []


def test_a_state_change_becomes_one_event(monkeypatch):
    _reset(monkeypatch)
    states = [{"light.kitchen": "off", "sensor.hall": "21.5"},
              {"light.kitchen": "on", "sensor.hall": "21.5"}]
    monkeypatch.setattr(evaluator, "_poll_entities", lambda: states.pop(0))
    assert evaluator.homeassistant_events() == []
    events = evaluator.homeassistant_events()
    assert events == [{"source": "homeassistant", "kind": "state_change",
                       "entity_id": "light.kitchen", "state": "on",
                       "previous_state": "off"}]


def test_a_new_entity_is_not_an_event(monkeypatch):
    """A battery swap or a new device appearing has no previous state to
    compare, so inventing one would fire change-rules on non-changes."""
    _reset(monkeypatch)
    states = [{"light.kitchen": "on"},
              {"light.kitchen": "on", "sensor.new": "42"}]
    monkeypatch.setattr(evaluator, "_poll_entities", lambda: states.pop(0))
    evaluator.homeassistant_events()
    assert evaluator.homeassistant_events() == []


# --- the CLI half of the seam --------------------------------------------------


def _load_cli():
    loader = importlib.machinery.SourceFileLoader(
        "agentbox_cli_eval", str(REPO / "cli" / "agentbox"))
    spec = importlib.util.spec_from_loader("agentbox_cli_eval", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["agentbox_cli_eval"] = module
    spec.loader.exec_module(module)
    return module


def test_cli_and_evaluator_fingerprints_agree():
    """Two implementations on either side of the container boundary. If they
    drift, every approval the CLI writes is void by the evaluator's reading —
    rules approve says yes and nothing ever fires."""
    cli = _load_cli()
    record = _rule("parity", when={"source": "homeassistant",
                                   "entity_id": "switch.washer",
                                   "state": "off"},
                   do=[{"tool": "speak_aloud",
                        "args": {"text": "done", "speaker": "kitchen"}}])
    assert cli.rule_fingerprint(record) == evaluator.fingerprint(record)
    assert tuple(cli.RULE_FINGERPRINT_FIELDS) == \
        tuple(evaluator.FINGERPRINT_FIELDS)


def test_cli_approval_is_readable_by_the_evaluator(tmp_path, monkeypatch):
    """The whole seam, end to end: approve with the CLI's writer, fire with
    the evaluator's reader."""
    _reset(monkeypatch)
    cli = _load_cli()
    approved = tmp_path / "policy" / "rules-approved.json"
    monkeypatch.setattr(cli, "RULES_APPROVED_PATH", approved)
    monkeypatch.setattr(cli, "RULES_DIR", tmp_path / "rules")
    record = _store(tmp_path / "rules", _rule("morning"))
    assert cli.rules_decide("morning", activate=True) == 0
    dispatch = Dispatch()
    assert _pass([TICK], dispatch, tmp_path) == 1
    # And deactivation revokes it.
    assert cli.rules_decide("morning", activate=False) == 0
    monkeypatch.setattr(evaluator, "_last_fired", {})
    assert _pass([TICK], dispatch, tmp_path) == 0
    assert len(dispatch.calls) == len(record["do"])  # no second firing


def test_the_cli_warning_agrees_with_the_live_sources():
    """The approve-time message names which sources are live. It is a plain
    tuple in cli/agentbox (the CLI cannot import the gateway's modules), so
    this is the check that keeps the two from drifting."""
    source = (REPO / "cli" / "agentbox").read_text()
    assert f'live = {tuple(evaluator.LIVE_SOURCES)!r}'.replace("'", '"') \
        in source.replace("'", '"')


# --- wiring --------------------------------------------------------------------


def test_evaluator_module_is_in_the_image():
    dockerfile = (APP.parents[1] / "agentbox-mcp/Dockerfile").read_text()
    assert "app/evaluator.py /app/evaluator.py" in dockerfile


def test_kill_switch_stops_the_thread(monkeypatch):
    monkeypatch.setattr(evaluator, "ENABLED", False)
    assert evaluator.start(lambda t, a: None) is False
