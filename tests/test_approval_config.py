"""Approval-loop configuration, now that it comes from the portal.

The loop decides whose Discord replies can grant a capability. Where that
list comes from, and what happens when it changes or goes missing, is the
whole security surface of this file.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest
from conftest import patch_everywhere, script_code

REPO = Path(__file__).resolve().parent.parent


def load_approvals(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOX_PORTAL_DIR", str(tmp_path))
    monkeypatch.delenv("AGENTBOX_APPROVAL_CHANNEL_ID", raising=False)
    monkeypatch.delenv("AGENTBOX_APPROVAL_USER_IDS", raising=False)
    loader = importlib.machinery.SourceFileLoader(
        "agentbox_approvals", str(REPO / "cli" / "agentbox-approvals"))
    spec = importlib.util.spec_from_loader("agentbox_approvals", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["agentbox_approvals"] = module
    spec.loader.exec_module(module)
    # Nothing in these tests should reach 1Password; a subprocess in the poll
    # loop is the regression this guards against as much as it is a test seam.
    patch_everywhere(monkeypatch, module, "op_read",
                        lambda ref: pytest.fail(f"unexpected op_read({ref})"))
    return module


@pytest.fixture
def approvals(tmp_path, monkeypatch):
    return load_approvals(tmp_path, monkeypatch)


def test_it_reads_the_portal_settings(approvals):
    approvals.SETTINGS.save({"approval_channel": "100000000000000001",
                             "approval_user_ids": "100000000000000002"})
    assert approvals.approval_channel() == "100000000000000001"
    assert approvals.approval_operators() == {"100000000000000002"}


def test_a_revoked_operator_stops_approving_without_a_restart(approvals):
    """Revocation has to be as live as granting.

    The loop re-reads each pass precisely so removing somebody on the portal
    takes effect now, rather than whenever a service nobody remembers gets
    restarted.
    """
    approvals.SETTINGS.save({"approval_user_ids": "111111111111111111,"
                                                  "222222222222222222"})
    assert len(approvals.approval_operators()) == 2
    approvals.SETTINGS.save({"approval_user_ids": "111111111111111111"})
    assert approvals.approval_operators() == {"111111111111111111"}


def test_an_empty_list_means_nobody_rather_than_the_last_good_one(approvals):
    """Fail closed. Keeping a stale list would be the dangerous direction."""
    approvals.SETTINGS.save({"approval_user_ids": "111111111111111111"})
    assert approvals.approval_operators()
    approvals.SETTINGS.save({"approval_user_ids": ""})
    sys.modules["agentbox_approvals_discord"]._op_operators_cache = ""  # nothing in the vault
    assert approvals.approval_operators() == set()


def test_the_vault_is_read_at_most_once(tmp_path, monkeypatch):
    """It is consulted from a 5-second poll loop, and op_read forks a process."""
    module = load_approvals(tmp_path, monkeypatch)
    calls = []

    def fake_op_read(ref):
        calls.append(ref)
        return "100000000000000002"

    patch_everywhere(monkeypatch, module, "op_read", fake_op_read)
    for _ in range(25):
        assert module.approval_operators() == {"100000000000000002"}
    assert len(calls) == 1, f"op_read called {len(calls)} times in 25 passes"


def test_a_portal_value_beats_the_vault(tmp_path, monkeypatch):
    """So moving a box onto the portal actually moves it."""
    module = load_approvals(tmp_path, monkeypatch)
    patch_everywhere(monkeypatch, module, "op_read",
                        lambda ref: pytest.fail("vault consulted anyway"))
    module.SETTINGS.save({"approval_user_ids": "999999999999999999"})
    assert module.approval_operators() == {"999999999999999999"}


def test_a_username_is_rejected_rather_than_stored(approvals):
    """A mistyped id silently stops working, so catch what we can up front."""
    settings = sys.modules["agentbox_settings"]
    with pytest.raises(settings.InvalidSetting) as caught:
        approvals.SETTINGS.save({"approval_user_ids": "alex#1234"})
    assert caught.value.key == "approval_user_ids"


def test_the_bot_token_is_not_a_portal_setting(approvals):
    """It stays in 1Password: rotation, an audit trail, and off this disk.

    Every other value moved *out* of a local file. Moving a managed secret
    *into* one would be the same trade in reverse.
    """
    settings = sys.modules["agentbox_settings"]
    keys = {s.key for s in settings.SETTINGS}
    assert not {k for k in keys if "bot" in k or "token" in k}
    # And it is still reachable where it does live, so this is a statement
    # about where the token belongs rather than about it having been dropped.
    # code_of, not read_text: this codebase has been bitten three times by a
    # source assertion matching the comment that explains the thing.
    assert "op://Agentbox/discord/bot_token" in script_code("agentbox-approvals")
