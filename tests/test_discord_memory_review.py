"""Reviewing memories from Discord, where the assistant is also listening.

This shares a process with the grant loop deliberately, because it needs
exactly the same protections and reimplementing them is how they drift. The
one thing it adds is a privacy rule the grant loop never needed: a grant is
about a tool and is nobody's secret, but a memory proposal can be private to
one person, and a shared channel is the wrong place for it.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    loader = importlib.machinery.SourceFileLoader(
        "agentbox_approvals", str(REPO / "cli" / "agentbox-approvals"))
    spec = importlib.util.spec_from_loader("agentbox_approvals", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["agentbox_approvals"] = module
    spec.loader.exec_module(module)
    return module


lib = _load()


class Recorder:
    """Captures what would be posted to Discord."""

    def __init__(self, replies=None):
        self.posts = []
        self.replies = replies or {}

    def __call__(self, method, path, token, payload=None):
        if method == "POST" and "/messages" in path:
            self.posts.append((path, (payload or {}).get("content", "")))
            return {"id": "posted"}
        if method == "POST" and path == "/users/@me/channels":
            return {"id": f"dm-{payload['recipient_id']}"}
        return self.replies.get((method, path), {})


def test_the_assistants_own_messages_are_never_acted_on():
    """The load-bearing property. An injected assistant that types 'remember
    <id>' into the channel it can also read must change nothing — so bot
    authorship is filtered before any verb is parsed."""
    source = (REPO / "cli" / "agentbox-approvals").read_text()
    loop = source.split("for message in reversed(messages):")[1]
    bot_check = loop.index('if author.get("bot"):')
    memory_handling = loop.index("handle_memory_reply")
    assert bot_check < memory_handling, \
        "memory replies are handled before bots are filtered out"
    # And the operator allowlist is also ahead of it.
    assert loop.index("not in operators") < memory_handling


def test_a_private_proposal_never_reaches_the_shared_channel(monkeypatch):
    """Posting one person's private proposal where the household reads it is
    a disclosure neither of them chose."""
    posted = Recorder()
    monkeypatch.setattr(lib, "discord", posted)
    monkeypatch.setattr(lib, "memory_call", lambda m, p, b=None: {
        "proposals": [{"id": "aaaabbbb-1", "scope": "sam", "kind": "memory",
                       "statement": "Sam's private thing"}]})
    monkeypatch.setenv("AGENTBOX_DISCORD_IDENTITIES", "sam:999")
    lib.announce_proposals("shared-channel", "tok", {})
    assert posted.posts, "nothing was posted at all"
    for path, content in posted.posts:
        assert "shared-channel" not in path, "private proposal hit the channel"
        assert "dm-999" in path
        assert "Sam's private thing" in content


def test_an_unmapped_private_proposal_is_not_posted_anywhere(monkeypatch):
    """No Discord id means no safe audience. It stays in the portal rather
    than defaulting to the channel."""
    posted = Recorder()
    monkeypatch.setattr(lib, "discord", posted)
    monkeypatch.setattr(lib, "memory_call", lambda m, p, b=None: {
        "proposals": [{"id": "aaaabbbb-1", "scope": "sam",
                       "statement": "Sam's private thing"}]})
    monkeypatch.setenv("AGENTBOX_DISCORD_IDENTITIES", "")
    lib.announce_proposals("shared-channel", "tok", {})
    assert posted.posts == []


def test_a_household_proposal_goes_to_the_channel(monkeypatch):
    posted = Recorder()
    monkeypatch.setattr(lib, "discord", posted)
    monkeypatch.setattr(lib, "memory_call", lambda m, p, b=None: {
        "proposals": [{"id": "ccccdddd-1", "scope": "household",
                       "kind": "memory", "statement": "Bin day is Wednesday"}]})
    lib.announce_proposals("shared-channel", "tok", {})
    path, content = posted.posts[0]
    assert "shared-channel" in path
    assert "ccccdddd" in content and "Bin day is Wednesday" in content


def test_a_proposal_is_announced_once(monkeypatch):
    posted = Recorder()
    monkeypatch.setattr(lib, "discord", posted)
    monkeypatch.setattr(lib, "memory_call", lambda m, p, b=None: {
        "proposals": [{"id": "ccccdddd-1", "scope": "household",
                       "statement": "x"}]})
    state = {}
    lib.announce_proposals("c", "tok", state)
    lib.announce_proposals("c", "tok", state)
    assert len(posted.posts) == 1


def test_a_feedback_proposal_says_so_in_the_prompt(monkeypatch):
    posted = Recorder()
    monkeypatch.setattr(lib, "discord", posted)
    monkeypatch.setattr(lib, "memory_call", lambda m, p, b=None: {
        "proposals": [{"id": "eeeeffff-1", "scope": "household",
                       "kind": "feedback",
                       "statement": "look_at_camera is broken"}]})
    lib.announce_proposals("c", "tok", {})
    assert "feedback about my behaviour" in posted.posts[0][1]


def test_an_ambiguous_id_prefix_acts_on_nothing(monkeypatch):
    """Two memories sharing four characters must not mean the wrong one is
    silently forgotten."""
    monkeypatch.setattr(lib, "memory_call", lambda m, p, b=None: {
        "proposals": [{"id": "aaaa1111"}, {"id": "aaaa2222"}],
        "memories": []})
    assert lib.resolve_memory("aaaa") == ("", "")


def test_an_unambiguous_prefix_resolves(monkeypatch):
    monkeypatch.setattr(lib, "memory_call", lambda m, p, b=None: (
        {"proposals": [{"id": "aaaa1111"}]} if "proposals" in p
        else {"memories": []}))
    assert lib.resolve_memory("aaaa1") == ("aaaa1111", "proposal")


def test_remember_approves_as_a_memory(monkeypatch):
    calls = []
    posted = Recorder()
    monkeypatch.setattr(lib, "discord", posted)
    monkeypatch.setattr(lib, "resolve_memory", lambda p: ("id-1", "proposal"))
    monkeypatch.setattr(lib, "memory_call",
                        lambda m, p, b=None: calls.append((m, p, b)) or {"ok": 1})
    assert lib.handle_memory_reply("remember", "id-1", "c", "tok", {}) is True
    assert calls[0][1].endswith("/approve")
    assert calls[0][2]["kind"] == "memory"


def test_feedback_files_it_as_feedback(monkeypatch):
    calls = []
    monkeypatch.setattr(lib, "discord", Recorder())
    monkeypatch.setattr(lib, "resolve_memory", lambda p: ("id-1", "proposal"))
    monkeypatch.setattr(lib, "memory_call",
                        lambda m, p, b=None: calls.append((m, p, b)) or {"ok": 1})
    lib.handle_memory_reply("feedback", "id-1", "c", "tok", {})
    assert calls[0][2]["kind"] == "feedback"


def test_forget_on_a_stored_memory_uses_the_forget_route(monkeypatch):
    calls = []
    monkeypatch.setattr(lib, "discord", Recorder())
    monkeypatch.setattr(lib, "resolve_memory", lambda p: ("id-9", "memory"))
    monkeypatch.setattr(lib, "memory_call",
                        lambda m, p, b=None: calls.append((m, p, b)) or {"ok": 1})
    lib.handle_memory_reply("forget", "id-9", "c", "tok", {})
    assert calls[0][1] == "/v1/memories/id-9/forget"


def test_an_unrelated_message_is_left_alone():
    assert lib.handle_memory_reply("approve", "archive_gmail", "c", "t", {}) is False
    assert lib.handle_memory_reply("hello", "", "c", "t", {}) is False
