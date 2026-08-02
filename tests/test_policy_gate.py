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

POLICY = REPO / "policies" / "runtime-actions.yaml"


@pytest.fixture
def tiers():
    return pg.load_tiers(POLICY)


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
    assert "list_tasks" in tiers["allowed"]
    assert "archive_gmail" in tiers["approval_required"]


def test_allowed_tool_passes(tiers, grants, consumed):
    pg.check("list_tasks", tiers, grants, consumed)


def test_unknown_tool_defaults_to_approval_required(tiers, grants, consumed):
    """Deny-by-default: a tool added without a tier must not run silently."""
    with pytest.raises(pg.PolicyDenied):
        pg.check("some_new_tool", tiers, grants, consumed)


def test_approval_required_denied_without_grant(tiers, grants, consumed):
    with pytest.raises(pg.PolicyDenied) as exc:
        pg.check("archive_gmail", tiers, grants, consumed)
    assert "agentbox grant archive_gmail" in str(exc.value)


def test_approval_required_passes_with_grant(tiers, grants, consumed):
    write_grant(grants, "archive_gmail")
    pg.check("archive_gmail", tiers, grants, consumed)


def test_single_use_grant_is_consumed(tiers, grants, consumed):
    write_grant(grants, "archive_gmail", single_use=True)
    pg.check("archive_gmail", tiers, grants, consumed)
    with pytest.raises(pg.PolicyDenied):
        pg.check("archive_gmail", tiers, grants, consumed)


def test_repeatable_grant_survives_use(tiers, grants, consumed):
    write_grant(grants, "archive_gmail", single_use=False)
    pg.check("archive_gmail", tiers, grants, consumed)
    pg.check("archive_gmail", tiers, grants, consumed)


def test_expired_grant_does_not_authorise(tiers, grants, consumed):
    write_grant(grants, "archive_gmail", ttl=-1)
    with pytest.raises(pg.PolicyDenied):
        pg.check("archive_gmail", tiers, grants, consumed)


def test_grant_for_one_tool_does_not_cover_another(tiers, grants, consumed):
    write_grant(grants, "archive_gmail")
    with pytest.raises(pg.PolicyDenied):
        pg.check("mark_gmail_read", tiers, grants, consumed)


def test_missing_grants_file_is_not_an_open_door(tiers, tmp_path, consumed):
    with pytest.raises(pg.PolicyDenied):
        pg.check("archive_gmail", tiers, tmp_path / "absent.json", consumed)


def test_corrupt_grants_file_is_not_an_open_door(tiers, grants, consumed):
    """A gate that fails open on malformed input is not a gate."""
    grants.write_text("{ this is not json")
    with pytest.raises(pg.PolicyDenied):
        pg.check("archive_gmail", tiers, grants, consumed)


def test_always_denied_cannot_be_granted(grants, consumed):
    tiers = {"always_denied": ["nuke"], "approval_required": [], "allowed": []}
    write_grant(grants, "nuke")
    with pytest.raises(pg.PolicyDenied) as exc:
        pg.check("nuke", tiers, grants, consumed)
    assert "cannot be approved at runtime" in str(exc.value)


def test_read_only_grants_file_does_not_block_consumption(tiers, grants, consumed):
    """Grants are mounted read-only in production; consumption is recorded
    elsewhere, so a read-only grants file must not break a legitimate call."""
    write_grant(grants, "archive_gmail")
    grants.chmod(0o444)
    try:
        pg.check("archive_gmail", tiers, grants, consumed)
    finally:
        grants.chmod(0o644)


def test_unwritable_consumption_store_refuses_rather_than_allows(tiers, grants, tmp_path):
    """The bug this replaced: a read-only mount silently turned every
    single-use grant into an unlimited TTL-long window."""
    write_grant(grants, "archive_gmail")
    blocked = tmp_path / "ro" / "consumed.json"
    blocked.parent.mkdir()
    blocked.parent.chmod(0o500)
    try:
        with pytest.raises(pg.PolicyDenied) as exc:
            pg.check("archive_gmail", tiers, grants, blocked)
        assert "cannot be enforced" in str(exc.value)
    finally:
        blocked.parent.chmod(0o700)


def test_every_live_mcp_tool_is_tiered(tiers):
    """A tool with no tier still fails closed, but silently — catch it here."""
    import re
    tiered = {t for entries in tiers.values() for t in entries}
    for mcp in ("vikunja-mcp", "memory-mcp", "google-workspace-mcp-lite"):
        src = (REPO / "services" / "compose" / mcp / "app" / "server.py").read_text()
        block = src.split("TOOLS = [", 1)[1].split("\ndef ", 1)[0]
        for name in re.findall(r'"name":\s*"([a-z_]+)"', block):
            assert name in tiered, f"{mcp} exposes untiered tool: {name}"
