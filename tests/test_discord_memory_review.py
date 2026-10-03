
"""Reviewing memories from Discord, where the assistant is also listening.

This shares a process with the grant loop because it needs the same
protections, and a second implementation of them could drift. It adds one
privacy rule the grant loop does not need. A grant is about a tool and is not
secret. A memory proposal can be private to one person, so it must not be
posted to a shared channel.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

from conftest import patch_everywhere, script_code

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
    """An injected assistant typing 'remember <id>' into the channel must change
    nothing, so bot messages are filtered before any command is read."""
    source = script_code("agentbox-approvals")
    loop = source.split("for message in reversed(messages):")[1]
    bot_check = loop.index('if author.get("bot"):')
    memory_handling = loop.index("handle_memory_reply")
    assert bot_check < memory_handling, \
        "memory replies are handled before bots are filtered out"
    # The operator allowlist is also checked first.
    assert loop.index("not in operators") < memory_handling


def test_a_private_proposal_never_reaches_the_shared_channel(monkeypatch):
    """Posting one person's private proposal where the household reads it
    would disclose it without that person's consent."""
    posted = Recorder()
    patch_everywhere(monkeypatch, lib, "discord", posted)
    patch_everywhere(monkeypatch, lib, "memory_call", lambda m, p, b=None: {
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
    patch_everywhere(monkeypatch, lib, "discord", posted)
    patch_everywhere(monkeypatch, lib, "memory_call", lambda m, p, b=None: {
        "proposals": [{"id": "aaaabbbb-1", "scope": "sam",
                       "statement": "Sam's private thing"}]})
    monkeypatch.setenv("AGENTBOX_DISCORD_IDENTITIES", "")
    lib.announce_proposals("shared-channel", "tok", {})
    assert posted.posts == []


def test_a_household_proposal_goes_to_the_channel(monkeypatch):
    posted = Recorder()
    patch_everywhere(monkeypatch, lib, "discord", posted)
    patch_everywhere(monkeypatch, lib, "memory_call", lambda m, p, b=None: {
        "proposals": [{"id": "ccccdddd-1", "scope": "household",
                       "kind": "memory", "statement": "Bin day is Wednesday"}]})
    lib.announce_proposals("shared-channel", "tok", {})
    path, content = posted.posts[0]
    assert "shared-channel" in path
    assert "ccccdddd" in content and "Bin day is Wednesday" in content


def test_a_proposal_is_announced_once(monkeypatch):
    posted = Recorder()
    patch_everywhere(monkeypatch, lib, "discord", posted)
    patch_everywhere(monkeypatch, lib, "memory_call", lambda m, p, b=None: {
        "proposals": [{"id": "ccccdddd-1", "scope": "household",
                       "statement": "x"}]})
    state = {}
    lib.announce_proposals("c", "tok", state)
    lib.announce_proposals("c", "tok", state)
    assert len(posted.posts) == 1


def test_a_feedback_proposal_says_so_in_the_prompt(monkeypatch):
    posted = Recorder()
    patch_everywhere(monkeypatch, lib, "discord", posted)
    patch_everywhere(monkeypatch, lib, "memory_call", lambda m, p, b=None: {
        "proposals": [{"id": "eeeeffff-1", "scope": "household",
                       "kind": "feedback",
                       "statement": "look_at_camera is broken"}]})
    lib.announce_proposals("c", "tok", {})
    assert "feedback about my behaviour" in posted.posts[0][1]


def test_an_ambiguous_id_prefix_acts_on_nothing(monkeypatch):
    """When two memories share a four-character prefix, the command must not
    act on either, or the wrong one could be forgotten."""
    patch_everywhere(monkeypatch, lib, "memory_call", lambda m, p, b=None: {
        "proposals": [{"id": "aaaa1111"}, {"id": "aaaa2222"}],
        "memories": []})
    assert lib.resolve_memory("aaaa") == ("", "")


def test_an_unambiguous_prefix_resolves(monkeypatch):
    patch_everywhere(monkeypatch, lib, "memory_call", lambda m, p, b=None: (
        {"proposals": [{"id": "aaaa1111"}]} if "proposals" in p
        else {"memories": []}))
    assert lib.resolve_memory("aaaa1") == ("aaaa1111", "proposal")


def test_remember_approves_as_a_memory(monkeypatch):
    calls = []
    posted = Recorder()
    patch_everywhere(monkeypatch, lib, "discord", posted)
    patch_everywhere(monkeypatch, lib, "resolve_memory", lambda p: ("id-1", "proposal"))
    patch_everywhere(monkeypatch, lib, "memory_call",
                        lambda m, p, b=None: calls.append((m, p, b)) or {"ok": 1})
    assert lib.handle_memory_reply("remember", "id-1", "c", "tok", {}) is True
    assert calls[0][1].endswith("/approve")
    assert calls[0][2]["kind"] == "memory"


def test_feedback_files_it_as_feedback(monkeypatch):
    calls = []
    patch_everywhere(monkeypatch, lib, "discord", Recorder())
    patch_everywhere(monkeypatch, lib, "resolve_memory", lambda p: ("id-1", "proposal"))
    patch_everywhere(monkeypatch, lib, "memory_call",
                        lambda m, p, b=None: calls.append((m, p, b)) or {"ok": 1})
    lib.handle_memory_reply("feedback", "id-1", "c", "tok", {})
    assert calls[0][2]["kind"] == "feedback"


def test_forget_on_a_stored_memory_uses_the_forget_route(monkeypatch):
    calls = []
    patch_everywhere(monkeypatch, lib, "discord", Recorder())
    patch_everywhere(monkeypatch, lib, "resolve_memory", lambda p: ("id-9", "memory"))
    patch_everywhere(monkeypatch, lib, "memory_call",
                        lambda m, p, b=None: calls.append((m, p, b)) or {"ok": 1})
    lib.handle_memory_reply("forget", "id-9", "c", "tok", {})
    assert calls[0][1] == "/v1/memories/id-9/forget"


def test_an_unrelated_message_is_left_alone():
    assert lib.handle_memory_reply("approve", "archive_gmail", "c", "t", {}) is False
    assert lib.handle_memory_reply("hello", "", "c", "t", {}) is False


# --- superseding from Discord --------------------------------------------------


def test_remember_replaces_links_them_in_one_step(monkeypatch):
    calls = []
    patch_everywhere(monkeypatch, lib, "discord", Recorder())
    patch_everywhere(monkeypatch, lib, "resolve_memory",
                        lambda p: (("new-1", "proposal") if p == "aaa"
                                   else ("old-1", "memory")))
    patch_everywhere(monkeypatch, lib, "memory_call",
                        lambda m, p, b=None: calls.append((p, b)) or
                        {"replaced": {"statement": "Bin day is Tuesday"}})
    assert lib.handle_memory_reply("remember", "aaa", "c", "tok", {},
                                   ["replaces", "bbb"]) is True
    path, body = calls[0]
    assert path.endswith("/approve")
    assert body["supersedes"] == "old-1"


def test_replaces_links_two_already_stored(monkeypatch):
    """The suggestion arrives after the write, so both memories are usually
    already stored."""
    calls = []
    patch_everywhere(monkeypatch, lib, "discord", Recorder())
    patch_everywhere(monkeypatch, lib, "resolve_memory",
                        lambda p: (("new-1", "memory") if p == "aaa"
                                   else ("old-1", "memory")))
    patch_everywhere(monkeypatch, lib, "memory_call",
                        lambda m, p, b=None: calls.append((p, b)) or
                        {"replaced": {"statement": "Bin day is Tuesday"}})
    lib.handle_memory_reply("replaces", "aaa", "c", "tok", {}, ["bbb"])
    assert calls[0][0] == "/v1/memories/new-1/supersede"
    assert calls[0][1]["supersedes"] == "old-1"


def test_a_suggestion_tells_you_exactly_what_to_type(monkeypatch):
    """A possible contradiction comes with the exact command to resolve it,
    so two conflicting facts are not left current."""
    posted = Recorder()
    patch_everywhere(monkeypatch, lib, "discord", posted)
    patch_everywhere(monkeypatch, lib, "resolve_memory", lambda p: ("new-12345678", "proposal"))
    patch_everywhere(monkeypatch, lib, "memory_call", lambda m, p, b=None: {
        "possibly_supersedes": [{"id": "old-87654321",
                                 "statement": "Bin day is Tuesday"}]})
    lib.handle_memory_reply("remember", "aaa", "c", "tok", {})
    content = posted.posts[-1][1]
    assert "may replace" in content
    assert "replaces new-1234 old-8765" in content


def test_replaces_without_a_target_asks_rather_than_guesses(monkeypatch):
    posted = Recorder()
    patch_everywhere(monkeypatch, lib, "discord", posted)
    patch_everywhere(monkeypatch, lib, "resolve_memory", lambda p: ("new-1", "memory"))
    lib.handle_memory_reply("replaces", "aaa", "c", "tok", {}, [])
    assert "which one it replaces" in posted.posts[-1][1]
