"""Tests for self-reflection: the outcome journal and the tool that reads it.

The assistant could propose memories but had no evidence to propose them from —
any "reflection" was the model recalling a conversation, which is biased toward
what went well, cannot count, and does not survive a restart.

Two properties matter more than the aggregation being right:

- **The journal must not become a second copy of the data.** Tool arguments
  carry email bodies and search strings; a log that records them turns a
  reflection feature into an exfiltration surface.
- **Denials must be recorded.** Approvals go through `agentbox grant`, which
  records; saying no used to just delete a file. A journal that remembers every
  yes and no no would make any tier argument read from it wrong in one
  direction.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MCP_APP = REPO / "services" / "templates" / "mcp"
sys.path.insert(0, str(MCP_APP))

POLICY = REPO / "policies" / "approval-policy.yaml"


def repo_policy_gate():
    """A policy_gate bound to the repo's policy, whatever ran before us.

    policy_gate resolves its paths into *default arguments* at import, and they
    default to the in-container locations. So an env var set here is too late
    if another test module imported it first — and one does. Reading the shipped
    policy explicitly is order-independent, which an env var is not.
    """
    import policy_gate as pg

    class Bound:
        load_tiers = staticmethod(lambda: pg.load_tiers(POLICY))
        load_tool_map = staticmethod(lambda: pg.load_tool_map(POLICY))

    return Bound


def load_outcome_log(path):
    """Fresh module bound to a temp journal — OUTCOME_FILE is read at import."""
    import os
    os.environ["MCP_OUTCOME_FILE"] = str(path)
    spec = importlib.util.spec_from_file_location(
        f"outcome_log_{path.name}", MCP_APP / "outcome_log.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def journal(tmp_path):
    return tmp_path / "outcomes.jsonl"


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# --- privacy ----------------------------------------------------------------


def test_argument_values_are_not_recorded(journal):
    """The whole reason this is safe to run against real accounts."""
    log = load_outcome_log(journal)
    log.record("google-mcp", "search_gmail", log.OK,
               arguments={"query": "invoice from acme legal counsel",
                          "max_results": 5})
    entry = records(journal)[0]
    serialised = json.dumps(entry)
    assert "acme" not in serialised and "invoice" not in serialised
    # Names are kept: which parameters the model uses is the useful part.
    assert entry["args"] == ["max_results", "query"]


def test_allowlisted_shape_values_are_recorded(journal):
    """`view` is bounded and carries no personal data, and whether the model
    ever chooses lean is one of the questions this log exists to answer."""
    log = load_outcome_log(journal)
    log.record("vikunja-mcp", "list_tasks", log.OK,
               arguments={"view": "lean", "search": "dentist appointment"})
    entry = records(journal)[0]
    assert entry["shape"] == {"view": "lean"}
    assert "dentist" not in json.dumps(entry)


def test_results_are_recorded_as_size_not_content(journal):
    log = load_outcome_log(journal)
    log.record("memory-mcp", "search_memories", log.OK, size=4096)
    entry = records(journal)[0]
    assert entry["bytes"] == 4096
    assert "content" not in entry and "result" not in entry


# --- the signal -------------------------------------------------------------


def test_denied_calls_are_recorded(journal):
    """Bridges never see a policy denial, so without this the most interesting
    events — the assistant wanting something it cannot have — are invisible."""
    log = load_outcome_log(journal)
    log.record("google-mcp", "set_home_climate", log.DENIED,
               capability="email_state_change")
    entry = records(journal)[0]
    assert entry["outcome"] == "denied"
    assert entry["capability"] == "email_state_change"


def test_operator_decisions_are_recorded_with_a_subject(journal):
    log = load_outcome_log(journal)
    log.record_decision("operator", "approve", "set_home_climate", "single-use")
    log.record_decision("operator", "deny", "set_home_climate")
    actions = [r["action"] for r in records(journal)]
    assert actions == ["approve", "deny"]


def test_a_failed_write_never_raises(tmp_path):
    """Recording an outcome must never break the call it describes."""
    unwritable = tmp_path / "nope" / "outcomes.jsonl"
    log = load_outcome_log(unwritable)
    log.record("svc", "tool", log.OK)  # must not raise


def test_rotation_keeps_one_generation(journal):
    log = load_outcome_log(journal)
    log.OUTCOME_MAX_BYTES = 200
    for i in range(80):
        log.record("svc", f"tool_{i}", log.OK)
    assert journal.exists()
    assert journal.with_suffix(".jsonl.1").exists()


# --- aggregation ------------------------------------------------------------


def load_memory_bridge():
    """The bridge module, which owns the aggregation."""
    path = REPO / "services" / "compose" / "memory-bridge" / "app" / "bridge.py"
    sys.path.insert(0, str(REPO / "services" / "templates" / "bridge" / "app"))
    spec = importlib.util.spec_from_file_location("memory_bridge_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_journal(directory, name, entries):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def test_aggregate_counts_outcomes_per_tool(tmp_path, monkeypatch):
    bridge = load_memory_bridge()
    now = int(time.time())
    write_journal(tmp_path, "a-outcomes.jsonl", [
        {"ts": now, "tool": "list_tasks", "outcome": "ok", "ms": 10},
        {"ts": now, "tool": "list_tasks", "outcome": "ok", "ms": 20},
        {"ts": now, "tool": "set_home_climate", "outcome": "denied", "ms": 1},
        {"ts": now, "tool": "find_or_create_task", "outcome": "invalid",
         "ms": 2, "detail": "missing_required_argument"},
    ])
    monkeypatch.setattr(bridge, "LOG_DIR", tmp_path)
    _, payload = bridge.activity(FakeHandler("?days=7"), None)
    assert payload["total_calls"] == 4
    assert payload["tools"]["list_tasks"]["ok"] == 2
    assert payload["tools"]["set_home_climate"]["denied"] == 1
    assert "find_or_create_task: missing_required_argument" in payload["problems"]


def test_aggregate_reads_every_service_journal(tmp_path, monkeypatch):
    bridge = load_memory_bridge()
    now = int(time.time())
    write_journal(tmp_path, "a-outcomes.jsonl", [{"ts": now, "tool": "x", "outcome": "ok"}])
    write_journal(tmp_path, "b-outcomes.jsonl", [{"ts": now, "tool": "y", "outcome": "ok"}])
    monkeypatch.setattr(bridge, "LOG_DIR", tmp_path)
    _, payload = bridge.activity(FakeHandler("?days=7"), None)
    assert set(payload["tools"]) == {"x", "y"}


def test_records_outside_the_window_are_excluded(tmp_path, monkeypatch):
    bridge = load_memory_bridge()
    old = int(time.time()) - 40 * 86400
    write_journal(tmp_path, "a-outcomes.jsonl", [{"ts": old, "tool": "x", "outcome": "ok"}])
    monkeypatch.setattr(bridge, "LOG_DIR", tmp_path)
    _, payload = bridge.activity(FakeHandler("?days=7"), None)
    assert payload["total_calls"] == 0


def test_an_empty_window_says_so_rather_than_looking_clean(tmp_path, monkeypatch):
    """A model reading an empty result will otherwise treat it as evidence of
    good behaviour rather than of no data."""
    bridge = load_memory_bridge()
    monkeypatch.setattr(bridge, "LOG_DIR", tmp_path)
    _, payload = bridge.activity(FakeHandler("?days=7"), None)
    assert "No activity" in payload["note"]


def test_a_torn_line_does_not_break_the_read(tmp_path, monkeypatch):
    """Append logs get truncated mid-write; one bad line must not lose the file."""
    bridge = load_memory_bridge()
    now = int(time.time())
    directory = tmp_path
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "a-outcomes.jsonl").write_text(
        json.dumps({"ts": now, "tool": "x", "outcome": "ok"}) + "\n{\"ts\": 1, \"to\n",
        encoding="utf-8")
    monkeypatch.setattr(bridge, "LOG_DIR", directory)
    _, payload = bridge.activity(FakeHandler("?days=7"), None)
    assert payload["total_calls"] == 1


def test_journal_lines_are_never_returned(tmp_path, monkeypatch):
    """Counts, not a replay. The journal omits argument values, but returning
    lines would still hand back timing and sequence for every call made."""
    bridge = load_memory_bridge()
    now = int(time.time())
    write_journal(tmp_path, "a-outcomes.jsonl", [
        {"ts": now, "tool": "search_gmail", "outcome": "ok",
         "args": ["query"], "shape": {"view": "lean"}}])
    monkeypatch.setattr(bridge, "LOG_DIR", tmp_path)
    _, payload = bridge.activity(FakeHandler("?days=7"), None)
    serialised = json.dumps(payload)
    assert "ts" not in payload and "args" not in serialised


class FakeHandler:
    """Minimal stand-in: activity() only reads the query string."""

    def __init__(self, query: str) -> None:
        self.path = f"/v1/activity{query}"


# --- wiring -----------------------------------------------------------------


def test_the_tool_is_mapped_to_a_capability():
    """Unmapped tools fail closed, which is right but silent."""
    import policy_gate as pg
    mapping = pg.load_tool_map(REPO / "policies" / "approval-policy.yaml")
    assert mapping.get("review_own_activity") == "inspect_service_logs"


def test_reflection_is_allowed_without_approval():
    """Reading counts about its own behaviour grants nothing, and a reflection
    that needs an approval per run will not happen."""
    import policy_gate as pg
    policy = REPO / "policies" / "approval-policy.yaml"
    tiers = pg.load_tiers(policy)
    assert "inspect_service_logs" in tiers["allowed"]


def test_the_bridge_declares_the_capability():
    """Or the authoritative gate is decorative."""
    src = (REPO / "services" / "compose" / "memory-bridge" / "app" / "bridge.py").read_text()
    assert "inspect_service_logs" in src and "def capability_for" in src


def test_the_skill_tells_it_to_propose_rather_than_act():
    """The permission model is only as good as the instructions that meet it."""
    skill = (REPO / "skills" / "self-reflection" / "SKILL.md").read_text()
    assert "propose_memory" in skill
    assert "review_own_activity" in skill
    # It must not read an empty window as a good report.
    assert "empty window means no data" in skill


# --- tier disambiguation ----------------------------------------------------
#
# From the first real run of the loop. The assistant saw set_home_climate refused
# and proposed "do not retry set_home_climate" — wrong: set_home_climate is
# approval_required and available with a grant, not always_denied. "denied"
# alone cannot distinguish "ask for this" from "never do this", and the
# safe-looking reading is the one that silently discards a capability.


def test_summary_labels_each_tool_with_its_tier(tmp_path, monkeypatch):
    bridge = load_memory_bridge()
    now = int(time.time())
    write_journal(tmp_path, "a-outcomes.jsonl", [
        {"ts": now, "tool": "set_home_climate", "outcome": "denied"},
        {"ts": now, "tool": "list_tasks", "outcome": "ok"},
    ])
    monkeypatch.setattr(bridge, "LOG_DIR", tmp_path)
    monkeypatch.setattr(bridge, "policy_gate", repo_policy_gate())
    _, payload = bridge.activity(FakeHandler("?days=7"), None)
    assert payload["tools"]["set_home_climate"]["tier"] == "approval_required"
    assert payload["tools"]["list_tasks"]["tier"] == "allowed"


def test_an_approvable_refusal_says_it_can_be_approved(tmp_path, monkeypatch):
    """The note is what stops a reflecting model reading a refusal as final."""
    bridge = load_memory_bridge()
    write_journal(tmp_path, "a-outcomes.jsonl", [
        {"ts": int(time.time()), "tool": "set_home_climate", "outcome": "denied"}])
    monkeypatch.setattr(bridge, "LOG_DIR", tmp_path)
    _, payload = bridge.activity(FakeHandler("?days=7"), None)
    assert "operator can approve" in payload["tools"]["set_home_climate"]["note"]


def test_a_missing_policy_omits_tiers_rather_than_failing(tmp_path, monkeypatch):
    """A summary that raises is worse than one missing a field."""
    bridge = load_memory_bridge()
    write_journal(tmp_path, "a-outcomes.jsonl", [
        {"ts": int(time.time()), "tool": "list_tasks", "outcome": "ok"}])
    monkeypatch.setattr(bridge, "LOG_DIR", tmp_path)
    monkeypatch.setattr(bridge, "policy_gate", None)
    _, payload = bridge.activity(FakeHandler("?days=7"), None)
    assert payload["tools"]["list_tasks"]["calls"] == 1
    assert "tier" not in payload["tools"]["list_tasks"]


def test_the_skill_warns_against_writing_off_an_approvable_tool():
    skill = (REPO / "skills" / "self-reflection" / "SKILL.md").read_text()
    assert "approval_required" in skill and "always_denied" in skill
    assert "how to ask" in skill


# --- a refusal is not a fault --------------------------------------------------
#
# On 2026-08-12 the assistant read its own journal and proposed, in writing:
# "look_at_camera returned upstream_rejected on every call this week (3/3, 0%
# success). This tool appears broken or restricted. Do not retry it." Every one
# of those calls was the bridge correctly refusing a camera nobody had
# configured. Separately, 45 propose_change "failures" were the smoke suite
# verifying that guarded files cannot be edited — guardrails working, recorded
# as breakage. The tools were fine; the record of them was not.


def test_a_bridge_refusal_is_recorded_as_denied_not_error():
    """403 means "no", and "no" is a different fact from "broken"."""
    source = (REPO / "services/templates/mcp/mcp_base.py").read_text()
    # The whole handler, not a fixed-width slice — a comment growing must
    # not silently move the code out of view and turn this green.
    block = source.split("except ToolError as exc:")[1].split("except Exception")[0]
    assert '"HTTP 403" in text' in block
    assert "outcome_log.DENIED, detail=\"upstream_refused\"" in block
    # And it must still be distinguishable from a policy-gate denial.
    assert "upstream_refused" != "denied"


def test_an_allowed_tool_that_was_refused_is_explained(tmp_path):
    """Without this note the summary shows a permitted tool with denials and
    no reason — and the available reading is 'it does not work'."""
    import os
    sys.path.insert(0, str(REPO / "services" / "templates" / "bridge" / "app"))
    os.environ["MEMORY_PATH"] = str(tmp_path / "memory.json")
    spec = importlib.util.spec_from_file_location(
        "mem_refusal", REPO / "services/compose/memory-bridge/app/bridge.py")
    mem = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mem)

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "agentbox-mcp-outcomes.jsonl").write_text("\n".join(
        json.dumps({"ts": int(time.time()), "service": "agentbox-mcp",
                    "tool": "look_at_camera", "outcome": "denied",
                    "detail": "upstream_refused",
                    "capability": "home_view_camera"})
        for _ in range(3)))
    mem.LOG_DIR = logs

    class Handler:
        headers = {}
        path = "/v1/activity?days=7"

    _, payload = mem.activity(Handler(), None)
    entry = payload["tools"]["look_at_camera"]
    assert entry["denied"] == 3
    if entry.get("tier") == "allowed":
        assert "not configured" in entry.get("note", "")
        assert "do not conclude it is unusable" in entry.get("note", "")
