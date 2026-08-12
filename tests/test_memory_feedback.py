"""Editing a proposal before it is saved, and telling memory from feedback.

Two things arrive through one door. "Sam is allergic to peanuts" is a fact
and belongs in memory. "Stop asking me to confirm before every calendar read"
is not a fact — it is a complaint about behaviour, and storing it as a memory
is a patch: the behaviour stays wrong and a line of context is spent every
session working around it. These test that the two stay separated, and that a
nearly-right memory can be corrected instead of thrown away.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "services" / "templates" / "bridge" / "app"))
sys.path.insert(0, str(REPO / "services" / "templates" / "mcp"))


def load_memory(tmp_path, review_token="review-secret"):
    os.environ["MEMORY_PATH"] = str(tmp_path / "memory.json")
    os.environ["MEMORY_REVIEW_TOKEN"] = review_token
    spec = importlib.util.spec_from_file_location(
        f"memfb_{abs(hash(str(tmp_path)))}",
        REPO / "services" / "compose" / "memory-bridge" / "app" / "bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Handler:
    def __init__(self, identity="alex", operator=True, query=""):
        self.headers = {}
        if identity:
            self.headers["X-Agentbox-Identity"] = identity
        if operator:
            self.headers["X-Memory-Review-Token"] = "review-secret"
        self.path = f"/v1/x{query}"


@pytest.fixture
def mem(tmp_path):
    return load_memory(tmp_path)


def propose(mem, statement, **extra):
    _, item = mem.create_proposal(Handler(), {"statement": statement, **extra})
    return item


# --- classification ------------------------------------------------------------


def test_a_fact_about_the_household_is_a_memory(mem):
    for statement in ("Sam is allergic to peanuts",
                      "The bins go out on Tuesday night",
                      "Alex prefers oat milk"):
        kind, _ = mem.classify_kind(statement)
        assert kind == mem.KIND_MEMORY, statement


def test_a_complaint_about_behaviour_is_feedback(mem):
    for statement in ("You keep asking me to confirm the same thing",
                      "Stop asking whether I want a summary",
                      "You should not read my email out loud",
                      "Always confirm before speaking after 9pm",
                      "I told you the meeting was moved and you got it wrong"):
        kind, reason = mem.classify_kind(statement)
        assert kind == mem.KIND_FEEDBACK, statement
        assert reason, "a classification with no reason cannot be reviewed"


def test_an_explicit_kind_beats_the_guess(mem):
    """The heuristic is allowed to be wrong; the human is not overruled."""
    kind, _, source = mem.resolve_kind({"kind": "memory"},
                                       "You always ask me twice")
    assert kind == mem.KIND_MEMORY
    assert source == "explicit"


def test_the_guess_is_labelled_as_a_guess(mem):
    item = propose(mem, "Alex prefers oat milk")
    assert item["kind_source"] == "auto"


# --- editing before saving -----------------------------------------------------


def test_a_proposal_can_be_corrected_before_it_is_saved(mem):
    """Rejecting a nearly-right memory was the only alternative, and that
    loses the information entirely."""
    item = propose(mem, "Alex doesn't like early meetings")
    mem.approve_proposal(Handler(), item["id"],
                         {"statement": "Alex doesn't like meetings before 10am"})
    store = mem.load_store()
    assert len(store["memories"]) == 1
    assert store["memories"][0]["statement"] == \
        "Alex doesn't like meetings before 10am"


def test_the_original_wording_survives_the_edit(mem):
    """A memory a human rewrote and one the assistant wrote are different
    evidence about how it is doing. Collapsing them corrupts that record."""
    item = propose(mem, "Alex hates meetings")
    mem.approve_proposal(Handler(), item["id"],
                         {"statement": "Alex dislikes meetings before 10am"})
    saved = mem.load_store()["memories"][0]
    assert saved["original_statement"] == "Alex hates meetings"
    assert saved["edited_by_reviewer"] is True


def test_approving_unchanged_records_no_edit(mem):
    item = propose(mem, "Alex prefers oat milk")
    mem.approve_proposal(Handler(), item["id"], {"statement": "Alex prefers oat milk"})
    saved = mem.load_store()["memories"][0]
    assert "edited_by_reviewer" not in saved
    assert "original_statement" not in saved


def test_an_edit_cannot_move_a_memory_into_someone_elses_plane(mem):
    """The reviewer may fix wording, not write into another person's private
    memory — the same rule that governs the assistant."""
    item = propose(mem, "Something about Alex")
    with pytest.raises(mem.BridgeError):
        mem.approve_proposal(Handler(identity="alex"), item["id"],
                             {"scope": "sam"})


# --- feedback routing ----------------------------------------------------------


def test_approving_feedback_does_not_create_a_memory(mem):
    """The whole point. Approving a behaviour complaint must not turn it into
    a memory that patches around the behaviour."""
    item = propose(mem, "You keep asking me the same question")
    assert item["kind"] == mem.KIND_FEEDBACK
    mem.approve_proposal(Handler(), item["id"], {})
    store = mem.load_store()
    assert store["memories"] == []
    assert len(store["feedback"]) == 1
    assert store["feedback"][0]["status"] == "open"


def test_the_reviewer_can_reclassify_a_memory_as_feedback(mem):
    item = propose(mem, "Alex prefers shorter answers")
    assert item["kind"] == mem.KIND_MEMORY
    mem.approve_proposal(Handler(), item["id"], {"kind": "feedback"})
    store = mem.load_store()
    assert store["memories"] == []
    assert len(store["feedback"]) == 1
    assert store["feedback"][0]["kind_source"] == "reviewer"


def test_the_reviewer_can_rescue_a_misclassified_memory(mem):
    """The heuristic will call some real facts feedback. Saving it as a memory
    must actually store a memory."""
    item = propose(mem, "Never give Sam peanuts")   # reads as a directive
    assert item["kind"] == mem.KIND_FEEDBACK
    mem.approve_proposal(Handler(), item["id"], {"kind": "memory"})
    store = mem.load_store()
    assert len(store["memories"]) == 1
    assert store.get("feedback", []) == []


def test_feedback_is_never_returned_as_memory(mem):
    """If the assistant could read the backlog it would explain the behaviour
    instead of the behaviour being fixed."""
    item = propose(mem, "You always ask twice")
    mem.approve_proposal(Handler(), item["id"], {})
    _, payload = mem.list_memories(Handler(), None)
    assert payload["memories"] == []


def test_the_backlog_needs_the_operator_token(mem):
    item = propose(mem, "You always ask twice")
    mem.approve_proposal(Handler(), item["id"], {})
    with pytest.raises(mem.BridgeError) as exc:
        mem.list_feedback(Handler(operator=False), None)
    assert exc.value.status == 403


def test_folding_records_what_was_actually_changed(mem):
    """A folded item with no note is indistinguishable from a forgotten one
    six months later."""
    item = propose(mem, "You keep asking me to confirm")
    mem.approve_proposal(Handler(), item["id"], {})
    mem.decide_feedback(Handler(), item["id"], "fold",
                        {"note": "removed the confirm step from the tool"})
    stored = mem.load_store()["feedback"][0]
    assert stored["status"] == "folded"
    assert stored["resolution"] == "removed the confirm step from the tool"
    _, open_items = mem.list_feedback(Handler(query="?status=open"), None)
    assert open_items["feedback"] == []


def test_dismissing_closes_it_without_a_fix(mem):
    item = propose(mem, "You always ask twice")
    mem.approve_proposal(Handler(), item["id"], {})
    mem.decide_feedback(Handler(), item["id"], "dismiss", {})
    assert mem.load_store()["feedback"][0]["status"] == "dismissed"


def test_the_same_complaint_is_not_queued_twice(mem):
    """The assistant cannot see the backlog, so left alone it would re-propose
    the same complaint every time the behaviour recurred."""
    item = propose(mem, "You keep asking me to confirm")
    mem.approve_proposal(Handler(), item["id"], {})
    again = propose(mem, "You keep asking me to confirm")
    assert again.get("note", "").startswith("already recorded")
    assert mem.load_store()["proposals"] == []


def test_approval_still_requires_the_review_token(mem):
    """Editing must not have opened a path around the gate."""
    item = propose(mem, "Alex prefers oat milk")
    with pytest.raises(mem.BridgeError) as exc:
        mem.approve_proposal(Handler(operator=False), item["id"],
                             {"statement": "anything"})
    assert exc.value.status == 403


# --- the real queue, as found on 2026-08-12 ------------------------------------
#
# Not invented examples. These are the seven proposals actually pending on the
# live box, and they are the reason the classifier has a second half: five of
# them are the assistant writing notes to itself about broken tooling, and the
# first version — tuned for a person complaining, "you keep asking me" —
# filed every one as a memory to be remembered forever.


REAL_FEEDBACK = [
    "find_or_create_task requires project_id as a required argument. Always "
    "call list_projects first (searching by title) to resolve a project name "
    "to its id before calling find_or_create_task.",
    "propose_change: 32/34 calls returned upstream_rejected in 7 days. Before "
    "proposing a change, always run mcp_builder_repo_run_repo_checks first.",
    "archive_gmail has tier approval_required and was refused 17 times this "
    "week — the capability exists, I just need a grant.",
    "Operator rejected 18 of my memory proposals in 7 days. Before proposing "
    "a memory, check: does this describe a durable pattern?",
    "look_at_camera returned upstream_rejected on every call this week (3/3, "
    "0% success). Do not retry it as a workaround.",
]

REAL_MEMORIES = [
    "Bins go out Tuesday",
    "Alex private test — Sam must not see this",
    "Sam is allergic to peanuts",
    "Alex prefers oat milk",
    "The boiler service is due in March",
]


@pytest.mark.parametrize("statement", REAL_FEEDBACK)
def test_the_assistants_notes_about_its_own_tools_are_feedback(mem, statement):
    """The purest case of the thing being separated: the fix for
    'look_at_camera is broken' is to repair look_at_camera, not to carry a
    memory forever saying it is broken."""
    kind, reason = mem.classify_kind(statement)
    assert kind == mem.KIND_FEEDBACK, statement[:60]
    assert reason


@pytest.mark.parametrize("statement", REAL_MEMORIES)
def test_household_facts_are_still_memories(mem, statement):
    """The other half of the corpus. A classifier that called everything
    feedback would 'pass' the test above and be useless."""
    assert mem.classify_kind(statement)[0] == mem.KIND_MEMORY, statement


def test_a_tool_name_alone_is_enough_to_suspect_feedback(mem):
    kind, reason = mem.classify_kind("set_home_climate seems unreliable")
    assert kind == mem.KIND_FEEDBACK
    assert "set_home_climate" in reason
