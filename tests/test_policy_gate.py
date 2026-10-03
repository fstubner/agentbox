"""Tests for runtime policy enforcement on tool calls, especially the ways a
gate like this can fail open."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from conftest import code_of

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "services" / "templates" / "mcp"))
import policy_gate as pg

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


# The gated example throughout is set_home_climate and home_control_climate,
# which stays gated because it costs money and can wake people.
# These tests are about the gate, not the example.


def test_shipped_policy_parses_and_has_all_tiers(tiers):
    assert set(tiers) == {"allowed", "approval_required", "always_denied"}
    assert "task_management" in tiers["allowed"]
    assert "home_control_climate" in tiers["approval_required"]
    assert "merge_own_pr" in tiers["always_denied"]


def test_one_policy_covers_operator_and_assistant_actions(tiers, tool_map):
    """Operator capabilities and assistant tools resolve from the same file,
    so the two cannot contradict each other."""
    assert "host_package_install" in tiers["approval_required"]   # operator
    assert tool_map["set_home_climate"] == "home_control_climate"      # assistant
    assert "home_control_climate" in tiers["approval_required"]


def test_tools_resolve_through_a_capability(tiers, tool_map):
    assert pg.tier_of("list_tasks", tiers, tool_map) == "allowed"
    assert pg.tier_of("set_home_climate", tiers, tool_map) == "approval_required"


def test_unmapped_tool_fails_closed(tiers, tool_map):
    assert pg.tier_of("brand_new_tool", tiers, tool_map) == "approval_required"


def test_tool_mapped_to_unknown_capability_fails_closed(tiers):
    assert pg.tier_of("weird", tiers, {"weird": "no_such_capability"}) == "approval_required"


def test_allowed_tool_passes(tiers, grants, consumed, tool_map):
    pg.check("list_tasks", tiers, grants, consumed, tool_map)


def test_unknown_tool_defaults_to_approval_required(tiers, grants, consumed, tool_map):
    """Deny by default. A tool added without a tier must not run."""
    with pytest.raises(pg.PolicyDenied):
        pg.check("some_new_tool", tiers, grants, consumed, tool_map)


def test_approval_required_denied_without_grant(tiers, grants, consumed, tool_map):
    with pytest.raises(pg.PolicyDenied) as exc:
        pg.check("set_home_climate", tiers, grants, consumed, tool_map)
    message = str(exc.value)
    assert "requires operator approval" in message
    # The message does not tell the model a command to suggest. The operator
    # is notified out of band. Naming the command here would invite the
    # assistant to relay an instruction it should not be composing.
    assert "agentbox grant" not in message


def test_approval_required_passes_with_grant(tiers, grants, consumed, tool_map):
    write_grant(grants, "set_home_climate")
    pg.check("set_home_climate", tiers, grants, consumed, tool_map)


def test_single_use_grant_is_consumed(tiers, grants, consumed, tool_map):
    write_grant(grants, "set_home_climate", single_use=True)
    pg.check("set_home_climate", tiers, grants, consumed, tool_map)
    with pytest.raises(pg.PolicyDenied):
        pg.check("set_home_climate", tiers, grants, consumed, tool_map)


def test_repeatable_grant_survives_use(tiers, grants, consumed, tool_map):
    write_grant(grants, "set_home_climate", single_use=False)
    pg.check("set_home_climate", tiers, grants, consumed, tool_map)
    pg.check("set_home_climate", tiers, grants, consumed, tool_map)


def test_expired_grant_does_not_authorise(tiers, grants, consumed, tool_map):
    write_grant(grants, "set_home_climate", ttl=-1)
    with pytest.raises(pg.PolicyDenied):
        pg.check("set_home_climate", tiers, grants, consumed, tool_map)


def test_grant_for_one_tool_does_not_cover_another(tiers, grants, consumed, tool_map):
    write_grant(grants, "set_home_climate")
    with pytest.raises(pg.PolicyDenied):
        # A different gated tool. A grant covers one capability only.
        pg.check("create_home_automation", tiers, grants, consumed, tool_map)


def test_missing_grants_file_is_not_an_open_door(tiers, tmp_path, consumed, tool_map):
    with pytest.raises(pg.PolicyDenied):
        pg.check("set_home_climate", tiers, tmp_path / "absent.json", consumed, tool_map)


def test_corrupt_grants_file_is_not_an_open_door(tiers, grants, consumed, tool_map):
    """The gate must fail closed on malformed input."""
    grants.write_text("{ this is not json")
    with pytest.raises(pg.PolicyDenied):
        pg.check("set_home_climate", tiers, grants, consumed, tool_map)


def test_always_denied_cannot_be_granted(grants, consumed):
    tiers = {"always_denied": ["nuke_cap"], "approval_required": [], "allowed": []}
    write_grant(grants, "nuke")
    with pytest.raises(pg.PolicyDenied) as exc:
        pg.check("nuke", tiers, grants, consumed, {"nuke": "nuke_cap"})
    assert "cannot be approved at runtime" in str(exc.value)


def test_read_only_grants_file_does_not_block_consumption(tiers, grants, consumed, tool_map):
    """Grants are mounted read-only in production. Consumption is recorded
    elsewhere, so a read-only grants file must not break a legitimate call."""
    write_grant(grants, "set_home_climate")
    grants.chmod(0o444)
    try:
        pg.check("set_home_climate", tiers, grants, consumed, tool_map)
    finally:
        grants.chmod(0o644)


def test_unwritable_consumption_store_refuses_rather_than_allows(tiers, grants, tmp_path, tool_map):
    """If consumption could not be recorded and the call were allowed, every
    single-use grant would become unlimited for its whole TTL."""
    write_grant(grants, "set_home_climate")
    blocked = tmp_path / "ro" / "consumed.json"
    blocked.parent.mkdir()
    blocked.parent.chmod(0o500)
    try:
        with pytest.raises(pg.PolicyDenied) as exc:
            pg.check("set_home_climate", tiers, grants, blocked, tool_map)
        assert "cannot be enforced" in str(exc.value)
    finally:
        blocked.parent.chmod(0o700)


def test_every_live_mcp_tool_is_mapped(tool_map):
    """An unmapped tool fails closed without saying why, so catch it here."""
    import re
    tiered = set(tool_map)
    integrations = REPO / "services" / "compose" / "agentbox-mcp" / "app" / "integrations"
    seen = 0
    for module in sorted(integrations.glob("*.py")):
        if module.name.startswith("_"):
            continue
        src = code_of(module)
# The live list only. A bare substring search for "TOOLS = [" also
        # matches RETIRED_TOOLS, and reading on to the next def takes in
        # whatever list follows, so retired tools would count as live.
        m = re.search(r"^TOOLS\b[^=\n]*=\s*\[", src, re.M)
        if not m:
            continue
        rest = src[m.end():]
        block = "" if rest.lstrip().startswith("]") else re.split(r"^\]", rest, maxsplit=1, flags=re.M)[0]
        for name in re.findall(r'"name":\s*"([a-z_]+)"', block):
            seen += 1
            assert name in tiered, f"{module.stem} exposes untiered tool: {name}"
    assert seen > 30, f"only found {seen} tools; the glob is probably wrong"


# --- bridge-side authoritative enforcement ----------------------------------


def test_capability_check_allows_an_allowed_capability(tiers, grants, consumed):
    pg.check_capability("task_management", tiers, grants, consumed)


def test_capability_check_denies_without_a_grant(tiers, grants, consumed):
    with pytest.raises(pg.PolicyDenied):
        pg.check_capability("home_control_climate", tiers, grants, consumed)


def test_grant_named_by_tool_authorises_the_capability(tiers, grants, consumed, tool_map):
    """The operator grants `set_home_climate` and the bridge asks for
    `home_control_climate`. One grant must satisfy both names."""
    write_grant(grants, "set_home_climate")
    pg.check_capability("home_control_climate", tiers, grants, consumed, tool_map=tool_map)


def test_non_consuming_check_leaves_the_grant_for_the_bridge(tiers, grants, consumed, tool_map):
    """agentbox-mcp checks without using up a single-use grant, so the bridge
    still has it. Otherwise every gated call would fail at the bridge, whose
    check is the authoritative one."""
    write_grant(grants, "set_home_climate")
    pg.check("set_home_climate", tiers, grants, consumed, tool_map, consume=False)
    pg.check_capability("home_control_climate", tiers, grants, consumed, tool_map=tool_map)
    with pytest.raises(pg.PolicyDenied):
        pg.check_capability("home_control_climate", tiers, grants, consumed, tool_map=tool_map)


def test_always_denied_capability_cannot_be_granted(grants, consumed):
    tiers = {"always_denied": ["nuke_cap"], "approval_required": [], "allowed": []}
    write_grant(grants, "nuke_cap")
    with pytest.raises(pg.PolicyDenied) as exc:
        pg.check_capability("nuke_cap", tiers, grants, consumed)
    assert "cannot be approved at runtime" in str(exc.value)


def test_google_bridge_declares_its_gated_capabilities():
    """Each bridge must name the capability an action uses, or the second gate
    has nothing to check.

    The bridge declares the capability and the policy file decides its tier,
    so moving a capability to `allowed` changes no bridge code.
    """
    src = (REPO / "services" / "compose" / "google-workspace-bridge" /
           "app" / "bridge.py").read_text()
    assert "def capability_for" in src
    assert "email_state_change" in src
    assert "email_label_own_namespace" in src


# --- the policy travels on a mount, not in the image ---------------------------


def _policy_services():
    """Services whose image contains the gate, so they enforce policy."""
    compose = REPO / "services" / "compose"
    out = []
    for dockerfile in sorted(compose.glob("*/Dockerfile")):
        if "policy_gate.py" in dockerfile.read_text(encoding="utf-8"):
            out.append(dockerfile.parent)
    return out


def test_no_image_bakes_the_policy():
    """The policy is read from the mount, not built into images, so editing it
    does not mark every service stale or ask for rebuilds that change nothing.
    """
    services = _policy_services()
    assert services, "no policy-enforcing services found; this test is blind"
    for service in services:
        body = (service / "Dockerfile").read_text(encoding="utf-8")
        assert "approval-policy.yaml" not in body, service.name


def test_every_enforcing_service_reads_the_mount():
    """Nothing is built in, so a service that enforces policy without mounting
    it would refuse every tool at runtime."""
    for service in _policy_services():
        compose = (service / "compose.yaml").read_text(encoding="utf-8")
        assert ":/policy:ro" in compose, f"{service.name} does not mount /policy"
        assert "AGENTBOX_RUNTIME_POLICY" in compose, service.name
