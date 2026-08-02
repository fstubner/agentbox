"""Tests for runtime policy enforcement at the MCP layer.

The architecture diagram always drew a policy engine, but nothing outside
cli/agentbox read the policy — so at runtime every assistant tool call was
ungated. These cover the gate that closes it, and specifically the ways a gate
like this fails open.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "services" / "templates" / "mcp"))
import policy_gate as pg  # noqa: E402

POLICY = REPO / "policies" / "approval-policy.yaml"


@pytest.fixture
def tiers():
    return pg.load_tiers(POLICY)


@pytest.fixture
def tool_map():
    return pg.load_tool_map(POLICY)


@pytest.fixture
def grants(tmp_path):
    return tmp_path / "grants.json"


@pytest.fixture
def consumed(tmp_path):
    return tmp_path / "consumed.json"


def write_grant(path, tool, ttl=60, single_use=True):
    path.write_text(json.dumps({"grants": [
        {"tool": tool, "expires_at": time.time() + ttl, "single_use": single_use}
    ]}))


def test_shipped_policy_parses_and_has_all_tiers(tiers):
    assert set(tiers) == {"allowed", "approval_required", "always_denied"}
    assert "task_management" in tiers["allowed"]
    assert "email_state_change" in tiers["approval_required"]
    assert "merge_own_pr" in tiers["always_denied"]


def test_one_policy_covers_operator_and_assistant_actions(tiers, tool_map):
    """The merge: operator capabilities and assistant tools resolve from the
    same file, so the two cannot contradict each other."""
    assert "host_package_install" in tiers["approval_required"]   # operator
    assert tool_map["archive_gmail"] == "email_state_change"      # assistant
    assert "email_state_change" in tiers["approval_required"]


def test_tools_resolve_through_a_capability(tiers, tool_map):
    assert pg.tier_of("list_tasks", tiers, tool_map) == "allowed"
    assert pg.tier_of("archive_gmail", tiers, tool_map) == "approval_required"


def test_unmapped_tool_fails_closed(tiers, tool_map):
    assert pg.tier_of("brand_new_tool", tiers, tool_map) == "approval_required"


def test_tool_mapped_to_unknown_capability_fails_closed(tiers):
    assert pg.tier_of("weird", tiers, {"weird": "no_such_capability"}) == "approval_required"


def test_allowed_tool_passes(tiers, grants, consumed, tool_map):
    pg.check("list_tasks", tiers, grants, consumed, tool_map)


def test_unknown_tool_defaults_to_approval_required(tiers, grants, consumed, tool_map):
    """Deny-by-default: a tool added without a tier must not run silently."""
    with pytest.raises(pg.PolicyDenied):
        pg.check("some_new_tool", tiers, grants, consumed, tool_map)


def test_approval_required_denied_without_grant(tiers, grants, consumed, tool_map):
    with pytest.raises(pg.PolicyDenied) as exc:
        pg.check("archive_gmail", tiers, grants, consumed, tool_map)
    assert "agentbox grant archive_gmail" in str(exc.value)


def test_approval_required_passes_with_grant(tiers, grants, consumed, tool_map):
    write_grant(grants, "archive_gmail")
    pg.check("archive_gmail", tiers, grants, consumed, tool_map)


def test_single_use_grant_is_consumed(tiers, grants, consumed, tool_map):
    write_grant(grants, "archive_gmail", single_use=True)
    pg.check("archive_gmail", tiers, grants, consumed, tool_map)
    with pytest.raises(pg.PolicyDenied):
        pg.check("archive_gmail", tiers, grants, consumed, tool_map)


def test_repeatable_grant_survives_use(tiers, grants, consumed, tool_map):
    write_grant(grants, "archive_gmail", single_use=False)
    pg.check("archive_gmail", tiers, grants, consumed, tool_map)
    pg.check("archive_gmail", tiers, grants, consumed, tool_map)


def test_expired_grant_does_not_authorise(tiers, grants, consumed, tool_map):
    write_grant(grants, "archive_gmail", ttl=-1)
    with pytest.raises(pg.PolicyDenied):
        pg.check("archive_gmail", tiers, grants, consumed, tool_map)


def test_grant_for_one_tool_does_not_cover_another(tiers, grants, consumed, tool_map):
    write_grant(grants, "archive_gmail")
    with pytest.raises(pg.PolicyDenied):
        pg.check("mark_gmail_read", tiers, grants, consumed, tool_map)


def test_missing_grants_file_is_not_an_open_door(tiers, tmp_path, consumed, tool_map):
    with pytest.raises(pg.PolicyDenied):
        pg.check("archive_gmail", tiers, tmp_path / "absent.json", consumed, tool_map)


def test_corrupt_grants_file_is_not_an_open_door(tiers, grants, consumed, tool_map):
    """A gate that fails open on malformed input is not a gate."""
    grants.write_text("{ this is not json")
    with pytest.raises(pg.PolicyDenied):
        pg.check("archive_gmail", tiers, grants, consumed, tool_map)


def test_always_denied_cannot_be_granted(grants, consumed):
    tiers = {"always_denied": ["nuke_cap"], "approval_required": [], "allowed": []}
    write_grant(grants, "nuke")
    with pytest.raises(pg.PolicyDenied) as exc:
        pg.check("nuke", tiers, grants, consumed, {"nuke": "nuke_cap"})
    assert "cannot be approved at runtime" in str(exc.value)


def test_read_only_grants_file_does_not_block_consumption(tiers, grants, consumed, tool_map):
    """Grants are mounted read-only in production; consumption is recorded
    elsewhere, so a read-only grants file must not break a legitimate call."""
    write_grant(grants, "archive_gmail")
    grants.chmod(0o444)
    try:
        pg.check("archive_gmail", tiers, grants, consumed, tool_map)
    finally:
        grants.chmod(0o644)


def test_unwritable_consumption_store_refuses_rather_than_allows(tiers, grants, tmp_path, tool_map):
    """The bug this replaced: a read-only mount silently turned every
    single-use grant into an unlimited TTL-long window."""
    write_grant(grants, "archive_gmail")
    blocked = tmp_path / "ro" / "consumed.json"
    blocked.parent.mkdir()
    blocked.parent.chmod(0o500)
    try:
        with pytest.raises(pg.PolicyDenied) as exc:
            pg.check("archive_gmail", tiers, grants, blocked, tool_map)
        assert "cannot be enforced" in str(exc.value)
    finally:
        blocked.parent.chmod(0o700)


def test_every_live_mcp_tool_is_mapped(tool_map):
    """An unmapped tool fails closed, but silently — catch it here."""
    import re
    tiered = set(tool_map)
    for mcp in ("vikunja-mcp", "memory-mcp", "google-workspace-mcp-lite"):
        src = (REPO / "services" / "compose" / mcp / "app" / "server.py").read_text()
        block = src.split("TOOLS = [", 1)[1].split("\ndef ", 1)[0]
        for name in re.findall(r'"name":\s*"([a-z_]+)"', block):
            assert name in tiered, f"{mcp} exposes untiered tool: {name}"
