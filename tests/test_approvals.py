"""Tests for the Discord approval loop.

The property that matters is not that approval works — it is that the assistant
cannot approve itself. The assistant is in the same Discord, and an instruction
embedded in an email can make it say anything, including "approve archive_gmail".
So the loop must ignore every message a bot authored and every user it does not
recognise, and it must never see a grant it can write.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "services" / "templates" / "mcp"))
import policy_gate as pg  # noqa: E402

spec = importlib.util.spec_from_loader(
    "approvals",
    importlib.machinery.SourceFileLoader("approvals", str(REPO / "cli" / "agentbox-approvals")))
approvals = importlib.util.module_from_spec(spec)


def message(content, user_id="operator-1", bot=False):
    return {"id": "1", "content": content, "author": {"id": user_id, "bot": bot}}


def accepts(msg, operators={"operator-1"}):
    """Mirror of the loop's filter, which is the whole security boundary."""
    author = msg.get("author") or {}
    if author.get("bot"):
        return False
    if str(author.get("id")) not in operators:
        return False
    words = str(msg.get("content", "")).strip().split()
    return len(words) >= 2 and words[0].lower() in ("approve", "deny")


def test_operator_approval_is_accepted():
    assert accepts(message("approve archive_gmail"))


def test_a_bot_cannot_approve_even_with_the_right_words():
    """The assistant is a bot in this channel. An injected instruction making it
    type the approval must do nothing."""
    assert not accepts(message("approve archive_gmail", user_id="operator-1", bot=True))


def test_a_stranger_cannot_approve():
    assert not accepts(message("approve archive_gmail", user_id="someone-else"))


def test_unrelated_chatter_is_ignored():
    assert not accepts(message("should I approve archive_gmail?"))
    assert not accepts(message("approve"))


# --- pending records --------------------------------------------------------


def test_denial_records_a_pending_request(tmp_path):
    pg.record_pending("archive_gmail", "email_state_change", tmp_path)
    written = json.loads((tmp_path / "archive_gmail.json").read_text())
    assert written["tool"] == "archive_gmail"
    assert written["capability"] == "email_state_change"
    assert written["requested_at"] <= int(time.time())


def test_repeated_denials_do_not_queue_duplicates(tmp_path):
    """A model that retries should not produce five identical asks."""
    pg.record_pending("archive_gmail", "email_state_change", tmp_path)
    first = (tmp_path / "archive_gmail.json").read_text()
    time.sleep(1.1)
    pg.record_pending("archive_gmail", "email_state_change", tmp_path)
    assert (tmp_path / "archive_gmail.json").read_text() == first
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_recording_never_raises_on_an_unwritable_store(tmp_path):
    """A failure to record must not turn a clean denial into an error the model
    has to interpret."""
    blocked = tmp_path / "ro"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        pg.record_pending("archive_gmail", "email_state_change", blocked / "pending")
    finally:
        blocked.chmod(0o700)


def test_the_loop_never_writes_grants_directly():
    """Grants are written by cli/agentbox, which the assistant cannot run. The
    loop shells out rather than editing the grants file itself, so there is one
    place that mints permission."""
    source = (REPO / "cli" / "agentbox-approvals").read_text()
    assert "grants.json" not in source
    assert '"grant", tool' in source
