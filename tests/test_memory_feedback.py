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


def test_a_proposal_written_before_kind_existed_is_still_classified(mem):
    """The store predates this feature. A proposal with no `kind` was read as
    a memory by `.get("kind") == KIND_FEEDBACK`, so the first real use of the
    feature approved three notes about broken tooling straight into durable
    memory — exactly what the split exists to prevent.
    """
    # Written the way the old bridge wrote them: no kind field at all.
    store = mem.load_store()
    store["proposals"].append({
        "id": "legacy-1", "scope": "alex", "status": "proposed",
        "statement": "look_at_camera returned upstream_rejected on every call"})
    mem.save_store(store)

    mem.approve_proposal(Handler(), "legacy-1", {})
    after = mem.load_store()
    assert after["memories"] == []
    assert len(after["feedback"]) == 1
    assert after["feedback"][0]["kind_source"] == "auto-at-approval"


def test_a_legacy_fact_still_becomes_a_memory(mem):
    """The rescue must not tip the other way and swallow real memories."""
    store = mem.load_store()
    store["proposals"].append({
        "id": "legacy-2", "scope": "alex", "status": "proposed",
        "statement": "Sam is allergic to peanuts"})
    mem.save_store(store)
    mem.approve_proposal(Handler(), "legacy-2", {})
    after = mem.load_store()
    assert len(after["memories"]) == 1
    assert after.get("feedback", []) == []


# --- the operator stating a fact -----------------------------------------------


def test_the_operator_can_state_a_fact_without_a_review_step(mem):
    """Reviewing your own statement is theatre. The gate exists so the
    assistant cannot write its own memory; a person typing at a terminal is
    the evidence it protects.
    """
    _, item = mem.create_memory(Handler(), {"statement": "Bin day is Wednesday",
                                            "scope": "household"})
    assert item["scope"] == "household"
    assert mem.load_store()["memories"][0]["statement"] == "Bin day is Wednesday"
    assert mem.load_store()["proposals"] == []


def test_the_assistant_still_cannot_use_that_path(mem):
    """The whole reason the direct write is operator-only."""
    with pytest.raises(mem.BridgeError) as exc:
        mem.create_memory(Handler(operator=False), {"statement": "sneaky"})
    assert exc.value.status == 403


# --- forgetting ----------------------------------------------------------------


def test_a_stored_memory_can_be_removed(mem):
    """Memory was append-only: a wrong fact stayed wrong forever, and these
    statements are read back as true, so a stale one misinforms every answer
    that touches it."""
    _, item = mem.create_memory(Handler(), {"statement": "Bin day is Tuesday",
                                            "scope": "household"})
    mem.forget_memory(Handler(), item["id"], {"reason": "it is Wednesday"})
    store = mem.load_store()
    assert store["memories"] == []
    assert store["forgotten"][0]["statement"] == "Bin day is Tuesday"
    assert store["forgotten"][0]["forgotten_reason"] == "it is Wednesday"


def test_the_assistant_cannot_forget_a_memory(mem):
    """Editing what it is allowed to remember by deleting the inconvenient
    parts is the same capability as writing memory, in reverse."""
    _, item = mem.create_memory(Handler(), {"statement": "x"})
    with pytest.raises(mem.BridgeError) as exc:
        mem.forget_memory(Handler(operator=False), item["id"], {})
    assert exc.value.status == 403


def test_forgetting_something_that_is_not_there_is_an_error(mem):
    with pytest.raises(mem.BridgeError) as exc:
        mem.forget_memory(Handler(), "no-such-id", {})
    assert exc.value.status == 404


def test_a_forgotten_memory_is_not_returned_to_the_assistant(mem):
    _, item = mem.create_memory(Handler(), {"statement": "Bin day is Tuesday",
                                            "scope": "household"})
    mem.forget_memory(Handler(), item["id"], {})
    _, payload = mem.list_memories(Handler(operator=False), None)
    assert payload["memories"] == []


# --- supersession --------------------------------------------------------------
#
# Three end states, not two. "Forgotten" collapsed two different facts into
# one: a memory that was never true, and a memory that was true and has been
# replaced. The second is history and worth keeping legible — it is what lets
# you see why an answer three weeks ago was right at the time.


def test_a_new_fact_retires_the_one_it_replaces(mem):
    _, old = mem.create_memory(Handler(), {"statement": "Bin day is Tuesday",
                                           "scope": "household"})
    _, new = mem.create_memory(Handler(), {"statement": "Bin day is Wednesday",
                                           "scope": "household",
                                           "supersedes": old["id"]})
    store = mem.load_store()
    retired = next(x for x in store["memories"] if x["id"] == old["id"])
    assert retired["status"] == mem.STATUS_SUPERSEDED
    assert retired["superseded_by"] == new["id"]
    assert new["replaced"]["statement"] == "Bin day is Tuesday"


def test_the_assistant_reads_only_the_current_version(mem):
    """Two contradictory memories with no marker of which is current is how a
    confident wrong answer happens."""
    _, old = mem.create_memory(Handler(), {"statement": "Bin day is Tuesday",
                                           "scope": "household"})
    mem.create_memory(Handler(), {"statement": "Bin day is Wednesday",
                                  "scope": "household", "supersedes": old["id"]})
    _, payload = mem.list_memories(Handler(operator=False), None)
    assert [m["statement"] for m in payload["memories"]] == ["Bin day is Wednesday"]


def test_the_chain_is_reachable_from_the_oldest_link(mem):
    """The id somebody has is usually the one they saw in an old answer."""
    _, first = mem.create_memory(Handler(), {"statement": "Bin day is Monday",
                                             "scope": "household"})
    _, second = mem.create_memory(Handler(), {"statement": "Bin day is Tuesday",
                                              "scope": "household",
                                              "supersedes": first["id"]})
    _, third = mem.create_memory(Handler(), {"statement": "Bin day is Wednesday",
                                             "scope": "household",
                                             "supersedes": second["id"]})
    for anchor in (first["id"], second["id"], third["id"]):
        _, history = mem.memory_history(Handler(), anchor, None)
        assert [h["statement"] for h in history["history"]] == [
            "Bin day is Monday", "Bin day is Tuesday", "Bin day is Wednesday"]
        assert history["current"] == third["id"]


def test_superseding_something_already_retired_is_refused(mem):
    _, old = mem.create_memory(Handler(), {"statement": "Bin day is Tuesday",
                                           "scope": "household"})
    mem.create_memory(Handler(), {"statement": "Bin day is Wednesday",
                                  "scope": "household", "supersedes": old["id"]})
    with pytest.raises(mem.BridgeError) as exc:
        mem.create_memory(Handler(), {"statement": "Bin day is Friday",
                                      "scope": "household",
                                      "supersedes": old["id"]})
    assert exc.value.status == 409


def test_superseding_something_that_does_not_exist_is_refused(mem):
    with pytest.raises(mem.BridgeError) as exc:
        mem.create_memory(Handler(), {"statement": "x", "supersedes": "nope"})
    assert exc.value.status == 404


def test_a_near_duplicate_is_suggested_not_applied(mem):
    """An automatic supersession that is wrong hides a true memory behind a
    false one and says nothing — strictly worse than leaving both visible."""
    mem.create_memory(Handler(), {"statement": "Bin day is Tuesday",
                                  "scope": "household"})
    _, new = mem.create_memory(Handler(), {"statement": "Bin day is Wednesday",
                                           "scope": "household"})
    assert new["possibly_supersedes"], "the obvious case produced no suggestion"
    assert "Tuesday" in new["possibly_supersedes"][0]["statement"]
    # Suggested only: both are still current until a human decides.
    _, payload = mem.list_memories(Handler(operator=False), None)
    assert len(payload["memories"]) == 2


def test_one_shared_word_is_enough_when_the_subject_matches(mem):
    """"Bin day is Tuesday" and "Bin day is Wednesday" share exactly one
    content word, because the words that differ are the whole point. Requiring
    two missed the case this feature exists for."""
    assert mem._same_subject("Bin day is Tuesday", "Bin day is Wednesday")
    assert not mem._same_subject("Sam is allergic to peanuts",
                                 "Bin day is Wednesday")


def test_unrelated_memories_are_not_suggested(mem):
    mem.create_memory(Handler(), {"statement": "Sam is allergic to peanuts",
                                  "scope": "household"})
    _, new = mem.create_memory(Handler(), {"statement": "Bin day is Wednesday",
                                           "scope": "household"})
    assert not new.get("possibly_supersedes")


def test_another_persons_memory_is_never_suggested(mem):
    """A suggestion naming someone else's private memory would leak it —
    the suggestion text quotes the statement it thinks you are replacing."""
    mem.create_memory(Handler(identity="sam"), {"statement": "Bin day is Tuesday",
                                                "scope": "sam"})
    _, new = mem.create_memory(Handler(), {"statement": "Bin day is Wednesday",
                                           "scope": "household"})
    assert not new.get("possibly_supersedes")


def test_approving_a_proposal_can_supersede(mem):
    _, old = mem.create_memory(Handler(), {"statement": "Bin day is Tuesday",
                                           "scope": "household"})
    item = propose(mem, "Bin day is Wednesday", scope="household")
    _, result = mem.approve_proposal(Handler(), item["id"],
                                     {"supersedes": old["id"]})
    assert result["replaced"]["statement"] == "Bin day is Tuesday"
    store = mem.load_store()
    assert next(x for x in store["memories"]
                if x["id"] == old["id"])["status"] == mem.STATUS_SUPERSEDED


def test_two_stored_memories_can_be_linked_after_the_fact(mem):
    """The suggestion arrives after the write, and somebody reviewing a list
    months later is looking at two memories that were never connected."""
    _, old = mem.create_memory(Handler(), {"statement": "Bin day is Tuesday",
                                           "scope": "household"})
    _, new = mem.create_memory(Handler(), {"statement": "Bin day is Wednesday",
                                           "scope": "household"})
    _, linked = mem.link_supersession(Handler(), new["id"],
                                      {"supersedes": old["id"]})
    assert linked["replaced"]["statement"] == "Bin day is Tuesday"
    _, history = mem.memory_history(Handler(), old["id"], None)
    assert [h["statement"] for h in history["history"]] == [
        "Bin day is Tuesday", "Bin day is Wednesday"]
    _, visible = mem.list_memories(Handler(operator=False), None)
    assert len(visible["memories"]) == 1


def test_a_memory_cannot_supersede_itself(mem):
    _, item = mem.create_memory(Handler(), {"statement": "x"})
    with pytest.raises(mem.BridgeError) as exc:
        mem.link_supersession(Handler(), item["id"], {"supersedes": item["id"]})
    assert exc.value.status == 400


def test_only_the_operator_can_link_a_supersession(mem):
    _, item = mem.create_memory(Handler(), {"statement": "x"})
    with pytest.raises(mem.BridgeError) as exc:
        mem.link_supersession(Handler(operator=False), item["id"],
                              {"supersedes": "anything"})
    assert exc.value.status == 403


# --- history the assistant can actually use ------------------------------------
#
# Excluding superseded versions outright was the first design and it was
# wrong. The argument — two contradictory memories produce a confident wrong
# answer — holds only when nothing says which is current. Nested under the
# fact that replaced it, with the date it stopped being true, there is no
# ambiguity left, and the assistant can answer "when did that change?" instead
# of flatly contradicting somebody who remembers the old value.


def test_the_current_fact_carries_its_own_history(mem):
    _, old = mem.create_memory(Handler(), {"statement": "Bin day is Tuesday",
                                           "scope": "household"})
    mem.create_memory(Handler(), {"statement": "Bin day is Wednesday",
                                  "scope": "household", "supersedes": old["id"]})
    _, payload = mem.list_memories(Handler(operator=False), None)
    assert len(payload["memories"]) == 1
    current = payload["memories"][0]
    assert current["statement"] == "Bin day is Wednesday"
    assert current["previously"][0]["statement"] == "Bin day is Tuesday"
    # The date it stopped being true is what removes the ambiguity.
    assert current["previously"][0]["until"]


def test_history_is_nested_not_a_second_current_memory(mem):
    """Flat inclusion is what makes a model contradict itself; nesting is
    what makes the same information safe."""
    _, old = mem.create_memory(Handler(), {"statement": "Bin day is Tuesday",
                                           "scope": "household"})
    mem.create_memory(Handler(), {"statement": "Bin day is Wednesday",
                                  "scope": "household", "supersedes": old["id"]})
    _, payload = mem.list_memories(Handler(operator=False), None)
    statements = [m["statement"] for m in payload["memories"]]
    assert statements == ["Bin day is Wednesday"]


def test_a_memory_with_no_history_carries_no_empty_field(mem):
    """Context economy: an empty list on every memory is pure cost."""
    mem.create_memory(Handler(), {"statement": "Sam is allergic to peanuts"})
    _, payload = mem.list_memories(Handler(operator=False), None)
    assert "previously" not in payload["memories"][0]


def test_a_long_chain_is_capped(mem):
    """A fact revised fifty times must not become fifty lines in every
    retrieval."""
    monkey = mem.MAX_PRIOR_VERSIONS
    _, item = mem.create_memory(Handler(), {"statement": "version 0",
                                            "scope": "household"})
    for n in range(1, monkey + 3):
        _, item = mem.create_memory(Handler(), {"statement": f"version {n}",
                                                "scope": "household",
                                                "supersedes": item["id"]})
    _, payload = mem.list_memories(Handler(operator=False), None)
    assert len(payload["memories"][0]["previously"]) == monkey


def test_the_management_view_still_returns_flat_rows(mem):
    """The portal builds chains itself and wants the raw rows."""
    _, old = mem.create_memory(Handler(), {"statement": "Bin day is Tuesday",
                                           "scope": "household"})
    mem.create_memory(Handler(), {"statement": "Bin day is Wednesday",
                                  "scope": "household", "supersedes": old["id"]})
    _, payload = mem.list_memories(
        Handler(query="?include_superseded=true"), None)
    assert len(payload["memories"]) == 2


def test_listing_never_writes_an_index_into_the_store(mem):
    """An index cached on the store is one save_store away from being written
    to the memory file on disk."""
    mem.create_memory(Handler(), {"statement": "x"})
    mem.list_memories(Handler(), None)
    assert "_index" not in mem.load_store()
    mem.create_memory(Handler(), {"statement": "y"})
    assert "_index" not in mem.load_store()
