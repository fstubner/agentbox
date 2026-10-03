"""Tests for memory scopes: a private scope per person, plus the household.

There are two scopes and no sharing between named people. With per-item
sharing, "what can Sam see?" could only be answered by reading every row.
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


def load_memory(tmp_path):
    os.environ["MEMORY_PATH"] = str(tmp_path / "memory.json")
    spec = importlib.util.spec_from_file_location(
        f"mem_{abs(hash(str(tmp_path)))}",
        REPO / "services" / "compose" / "memory-bridge" / "app" / "bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Handler:
    """Stand-in carrying the gateway's asserted identity header."""

    def __init__(self, identity: str = "", query: str = "") -> None:
        self.headers = {"X-Agentbox-Identity": identity} if identity else {}
        self.path = f"/v1/x{query}"


@pytest.fixture
def mem(tmp_path):
    return load_memory(tmp_path)


# --- scope resolution ----------------------------------------------------------


def test_memories_are_private_by_default(mem):
    """A memory placed in the shared plane only because no scope was given
    would be disclosed without anyone choosing to."""
    assert mem.resolve_scope({}, "alex") == "alex"
    assert mem.resolve_scope({"scope": ""}, "alex") == "alex"


def test_household_must_be_asked_for_explicitly(mem):
    assert mem.resolve_scope({"scope": "household"}, "alex") == "household"


def test_private_aliases_resolve_to_the_caller(mem):
    for alias in ("private", "me", "self"):
        assert mem.resolve_scope({"scope": alias}, "sam") == "sam"


def test_writing_into_another_persons_plane_is_refused(mem):
    """Writing into someone else's scope is impersonation, not sharing."""
    with pytest.raises(mem.BridgeError) as exc:
        mem.resolve_scope({"scope": "alex"}, "sam")
    assert exc.value.status == 403


def test_single_operator_writes_to_household(mem):
    """With no identities configured everything belongs to the one operator,
    and household is the plane they can always read."""
    assert mem.resolve_scope({}, "") == "household"


# --- visibility ------------------------------------------------------------------


def test_one_identity_cannot_read_anothers_private_memories(mem):
    items = [
        {"statement": "alex private", "scope": "alex"},
        {"statement": "sam private", "scope": "sam"},
        {"statement": "shared", "scope": "household"},
    ]
    alex = [i["statement"] for i in mem.visible_to(items, "alex")]
    sam = [i["statement"] for i in mem.visible_to(items, "sam")]
    assert alex == ["alex private", "shared"]
    assert sam == ["sam private", "shared"]


def test_visible_scopes_are_own_plus_household(mem):
    assert mem.visible_scopes("alex") == {"alex", "household"}
    assert mem.visible_scopes("") == {"household"}


def test_pre_scope_memories_read_as_household(mem):
    """These were written by a single user. Hiding them without notice would
    be worse than sharing them between two people who already share a house."""
    items = [{"statement": "legacy", "id": "1"}]
    assert len(mem.visible_to(items, "alex")) == 1
    assert len(mem.visible_to(items, "sam")) == 1


def test_filtering_happens_in_the_bridge_not_the_caller(mem):
    """If the gateway only asked for its own memories, any upstream mistake
    about who it was serving would leak another person's memories."""
    import inspect
    # Both list endpoints must filter, not just one.
    for endpoint in (mem.list_proposals, mem.list_memories):
        assert "visible_to(" in inspect.getsource(endpoint), endpoint.__name__


# --- end to end through the routes -------------------------------------------------


def test_a_proposal_records_the_writers_scope(mem):
    status, payload = mem.create_proposal(Handler("alex"),
                                          {"statement": "mine"})
    assert status == 201
    assert payload["scope"] == "alex"


def test_listing_is_scoped_to_the_caller(mem):
    mem.create_proposal(Handler("alex"), {"statement": "alex only"})
    mem.create_proposal(Handler("sam"), {"statement": "sam only"})
    mem.create_proposal(Handler("alex"), {"statement": "ours",
                                           "scope": "household"})

    _, alex = mem.list_proposals(Handler("alex"), None)
    _, sam = mem.list_proposals(Handler("sam"), None)
    alex_seen = {p["statement"] for p in alex["proposals"]}
    sam_seen = {p["statement"] for p in sam["proposals"]}

    assert alex_seen == {"alex only", "ours"}
    assert sam_seen == {"sam only", "ours"}
    assert "alex only" not in sam_seen


def test_identity_comes_from_the_header_not_the_body(mem):
    """The gateway asserts identity. A body field claiming to be someone must
    not be able to write into their plane."""
    with pytest.raises(mem.BridgeError):
        mem.create_proposal(Handler("sam"),
                            {"statement": "x", "scope": "alex"})


# --- whoami --------------------------------------------------------------------------


def test_whoami_reports_the_session_identity(mem):
    _, payload = mem.whoami(Handler("sam"), None)
    assert payload["identity"] == "sam"
    assert set(payload["memory_scopes_readable"]) == {"sam", "household"}
    assert payload["memory_scope_default"] == "sam"


def test_whoami_says_the_identity_cannot_be_changed(mem):
    """The assistant needs to know this, or it will try to change it."""
    _, payload = mem.whoami(Handler("alex"), None)
    assert "cannot act as anyone else" in payload["note"]


def test_whoami_needs_no_approval(mem):
    """The tool prevents mistakes caused by the assistant being unsure who it
    is acting for, so it must always be available."""
    handler = Handler("alex")
    assert mem.MemoryBridge.capability_for(handler, "GET", "/v1/whoami", None) is None


def test_whoami_reports_single_operator_honestly(mem):
    _, payload = mem.whoami(Handler(""), None)
    assert payload["identity"] is None
    assert payload["mode"] == "single-operator"


def test_operator_review_sees_every_scope(mem):
    """The reviewer must see every proposal, since they are the only one who
    can approve them. Otherwise private memory would be write-only, and an
    unreachable queue looks the same as an empty one.
    """
    items = [{"scope": "alex", "statement": "a"},
             {"scope": "sam", "statement": "b"},
             {"scope": "household", "statement": "c"}]

    assert len(mem.visible_to(items, "", operator=True)) == 3
    assert mem.visible_scopes("alex", operator=True) is None

    # Without the review credential nothing changes: scope still constrains.
    assert len(mem.visible_to(items, "alex")) == 2
    assert len(mem.visible_to(items, "")) == 1


def test_operator_flag_comes_from_the_review_token_not_a_header(mem):
    """Claiming to be the operator must require the operator's secret.

    If a plain header were enough, the assistant could set it and read every
    person's private memory.
    """
    import inspect
    source = inspect.getsource(mem.is_operator)
    assert "compare_digest" in source and "review_token()" in source

    class Fake:
        headers = {"X-Agentbox-Operator": "true", "X-Memory-Review-Token": "wrong"}

    assert mem.is_operator(Fake()) is False
