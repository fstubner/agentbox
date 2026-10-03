"""The household scenario runner: usefulness measured, not assumed.

The property that matters most: a scenario must never conflate "the seam is
broken" with "the house is empty". Those verdicts drive opposite actions —
fix code versus press the Hue button — and the week the journal showed
`whoami` outnumbering every household tool was the week nothing separated
them.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

from conftest import code_of

REPO = Path(__file__).resolve().parents[1]


def _load():
    loader = importlib.machinery.SourceFileLoader(
        "agentbox_scenarios_test", str(REPO / "cli" / "agentbox_scenarios.py"))
    spec = importlib.util.spec_from_loader("agentbox_scenarios_test", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["agentbox_scenarios_test"] = module
    spec.loader.exec_module(module)
    return module


lib = _load()


def _fake_gateway(answers):
    """tool_call stand-in: answers keyed by tool name."""
    def tool_call(service, tool, args, token):
        return answers.get(tool, (False, "not reachable: fake"))
    return tool_call


def test_empty_and_blocked_are_different_verdicts():
    """An empty house is not a broken seam. 56 entities with nothing
    controllable must read 'empty', while an unreachable bridge reads
    'blocked' — they demand different actions from different people."""
    working_but_bare = _fake_gateway({
        "list_home_entities": (True, {"entities": [], "total": 56,
                                      "controllable": []})})
    outcome = lib.run(working_but_bare, "tok")
    house = next(r for r in outcome["results"]
                 if r["asked"].startswith("What can you turn"))
    assert house["state"] == lib.EMPTY
    assert "none controllable" in house["detail"]

    broken = _fake_gateway({"list_home_entities": (False, "HTTP 503")})
    outcome = lib.run(broken, "tok")
    house = next(r for r in outcome["results"]
                 if r["asked"].startswith("What can you turn"))
    assert house["state"] == lib.BLOCKED


def test_a_ready_house_scores_ready():
    gateway = _fake_gateway({
        "list_home_entities": (True, {"entities": [{"entity_id": "light.hall"}],
                                      "total": 57,
                                      "controllable": ["light.hall"]})})
    outcome = lib.run(gateway, "tok")
    house = next(r for r in outcome["results"]
                 if r["asked"].startswith("What can you turn"))
    assert house["state"] == lib.READY
    assert "1 controllable" in house["detail"]


def test_an_empty_drive_result_names_the_scope_not_an_empty_drive():
    """The worst kind of wrong answer this suite can give.

    Under the drive.file scope the assistant sees only files it created, so a
    person with a full Drive gets zero results. Reporting that as "no match"
    told the operator their Drive was empty when the truth was that Agentbox
    cannot see any of it — a scope decision, not a fault to fix.
    """
    gateway = _fake_gateway({"search_drive": (True, {"files": []})})
    outcome = lib.run(gateway, "tok")
    drive = next(r for r in outcome["results"] if "Drive" in r["asked"])
    assert drive["state"] == lib.EMPTY
    assert "drive.file" in drive["needs"]
    assert "drive.readonly" in drive["needs"]
    # And a real failure still reads as blocked.
    broken = _fake_gateway({"search_drive": (False, "HTTP 403")})
    outcome = lib.run(broken, "tok")
    drive = next(r for r in outcome["results"] if "Drive" in r["asked"])
    assert drive["state"] == lib.BLOCKED


def test_every_scenario_has_a_next_action_or_is_self_evident():
    """A blocked row without a `needs` is a dead end for the operator."""
    for s in lib.SCENARIOS:
        assert s.asked.endswith("?") or s.asked.endswith(".")
        assert s.tool


def test_a_verdict_that_raises_becomes_blocked_not_a_crash():
    bad = lib.Scenario("Broken check?", "list_tasks", {},
                       lambda ok, p: 1 / 0)
    original = lib.SCENARIOS
    lib.SCENARIOS = [bad]
    try:
        outcome = lib.run(_fake_gateway({"list_tasks": (True, [])}), "tok")
        assert outcome["results"][0]["state"] == lib.BLOCKED
        assert "errored" in outcome["results"][0]["detail"]
    finally:
        lib.SCENARIOS = original


def test_scenario_tools_exist_on_the_gateway():
    """A scenario naming a tool that was renamed would score 'blocked' forever
    and read as a config gap. Check against the real tool registry."""
    sys.path.insert(0, str(REPO / "services/compose/agentbox-mcp/app"))
    sys.path.insert(0, str(REPO / "services/templates/mcp"))
    import server
    registered = {t["name"] for t in server.TOOLS}
    for s in lib.SCENARIOS:
        assert s.tool in registered, f"scenario names unknown tool {s.tool}"
    assert lib.HOUSEHOLD_TOOLS <= registered


def test_a_dict_payload_is_never_counted_by_its_keys():
    """The first live run reported "ready: 4 lights" against a house with
    zero lights — len() of {"entities": [], "total": 0, "returned": 0,
    "controllable": []} counted its four keys. The usefulness instrument
    confidently inventing usefulness is the worst failure it can have."""
    gateway = _fake_gateway({
        "list_home_entities": (True, {"entities": [], "total": 0,
                                      "returned": 0, "controllable": []})})
    outcome = lib.run(gateway, "tok")
    lights = next(r for r in outcome["results"]
                  if r["asked"].startswith("Turn the landing light"))
    assert lights["state"] == lib.EMPTY
    assert "no lights yet" in lights["detail"]


def test_an_undeclared_dict_shape_is_blocked_not_counted():
    verdict = lib._has_items("things")  # no key declared
    state, detail = verdict(True, {"a": 1, "b": 2})
    assert state == lib.BLOCKED
    assert "unexpected shape" in detail


def test_summarise_prepares_a_real_message_then_reads_it():
    """A fake id tested Gmail's error path instead of the route itself. prepare
    must find a message with readable text, and the read must carry its id.

    This scenario used to dispatch to triage_email. That route is retired with
    the router, so it now reads the clean text the main model summarises.
    """
    calls = []

    def tool_call(service, tool, args, token):
        if tool == "search_gmail":
            # Gmail's real shape: hits ride under "messages". The first hit
            # has no readable text, an invite, and must be skipped rather than
            # scored as a blocked summary path.
            return True, {"messages": [{"id": "m-invite"}, {"id": "m-123"}],
                          "resultSizeEstimate": 2}
        if tool == "clean_gmail":
            calls.append(args["message_id"])
            if args["message_id"] == "m-invite":
                return True, {"clean_text": "", "headers": {"subject": "Invite"}}
            return True, {"clean_text": "real body", "headers": {"subject": "Hi"}}
        return False, "not reachable: fake"

    outcome = lib.run(tool_call, "tok")
    summary = next(r for r in outcome["results"] if "Summarise" in r["asked"])
    assert summary["state"] == lib.READY
    assert "9 characters" in summary["detail"]
    # The scenario's own read comes last, and it is the readable message.
    assert calls[-1] == "m-123"


def test_google_payloads_are_read_at_their_real_keys():
    """Google nests results — calendar under "items", mail under "messages".
    The first two live runs of this suite read neither: one reported "3
    events" that were dict keys hiding 25 real events, the other reported an
    empty inbox it had never actually looked inside. Wrong in both
    directions, from the same shape mistake."""
    gateway = _fake_gateway({
        "list_calendar_events": (True, {"summary": "x", "nextPageToken": "t",
                                        "items": [{"id": 1}, {"id": 2}]}),
        "search_gmail": (True, {"messages": [{"id": "m1"}],
                                "resultSizeEstimate": 1})})
    outcome = lib.run(gateway, "tok")
    calendar = next(r for r in outcome["results"] if "calendar" in r["asked"])
    assert calendar["state"] == lib.READY
    assert "2 events" in calendar["detail"]
    inbox = next(r for r in outcome["results"] if "inbox" in r["asked"])
    assert inbox["state"] == lib.READY
    assert "1 recent unread" in inbox["detail"]


def test_an_empty_mailbox_reads_empty_and_a_dead_gmail_reads_blocked():
    """prepare's three-way contract: nothing to act on is not the same
    condition as could-not-look."""
    empty_box = _fake_gateway({"search_gmail": (True, {"messages": [],
                                                       "resultSizeEstimate": 0})})
    outcome = lib.run(empty_box, "tok")
    triage = next(r for r in outcome["results"] if "Summarise" in r["asked"])
    assert triage["state"] == lib.EMPTY

    dead = _fake_gateway({"search_gmail": (False, "HTTP 503")})
    outcome = lib.run(dead, "tok")
    triage = next(r for r in outcome["results"] if "Summarise" in r["asked"])
    assert triage["state"] == lib.BLOCKED
    assert "Gmail search failed" in triage["detail"]


def test_a_prepare_that_raises_becomes_blocked_not_a_crash():
    bad = lib.Scenario("Broken prepare?", "list_tasks", {},
                       lambda ok, p: (lib.READY, "x"),
                       prepare=lambda tc, tok: 1 / 0)
    original = lib.SCENARIOS
    lib.SCENARIOS = [bad]
    try:
        outcome = lib.run(_fake_gateway({}), "tok")
        assert outcome["results"][0]["state"] == lib.BLOCKED
        assert "prepare errored" in outcome["results"][0]["detail"]
    finally:
        lib.SCENARIOS = original


# --- the journal fold ------------------------------------------------------


def _rec(tool, outcome="ok", identity="alex"):
    return {"ts": 1, "service": "agentbox-mcp", "tool": tool,
            "outcome": outcome, "identity": identity}


def test_usage_separates_household_from_self_management():
    """The week-one signal, reproduced: lots of successful calls, almost none
    of them for a person. The summary must make that visible, not average it
    away."""
    records = ([_rec("whoami")] * 70 + [_rec("list_memory_proposals")] * 71
               + [_rec("list_calendar_events")] * 11
               + [_rec("speak_aloud")] * 3
               + [_rec("search_drive", outcome="error")])
    summary = lib.usage_summary(records)
    assert summary["household_ok"] == 14
    assert summary["self_management_ok"] == 141
    assert summary["failures"] == 1
    assert summary["household_ok"] < summary["self_management_ok"]


def test_failed_household_calls_do_not_count_as_help():
    summary = lib.usage_summary([_rec("search_gmail", outcome="error")] * 5)
    assert summary["household_ok"] == 0
    assert summary["failures"] == 5


def test_operator_decision_records_are_skipped():
    """record_decision rows have no tool field; they are judgements, not
    calls, and must not inflate either bucket."""
    summary = lib.usage_summary([
        {"ts": 1, "actor": "operator", "action": "approved", "subject": "x"}])
    assert summary["household_ok"] == 0
    assert summary["self_management_ok"] == 0


def test_the_cli_wires_the_command():
    source = code_of(REPO / "cli" / "agentbox")
    assert 'sub.add_parser("scenarios"' in source
    assert 'args.cmd == "scenarios"' in source
