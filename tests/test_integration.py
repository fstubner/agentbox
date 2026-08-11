"""End-to-end tests against the running system.

## Why these exist, and why they are not more unit tests

Every bug found during the 2026-08-06 session was a *seam* bug — two components
that each worked and disagreed with each other:

- the review CLI and the memory bridge disagreed about who the operator was,
  so private proposals were unapprovable and the queue rendered as empty
- the portal read `AGENTBOX_MEMORY_BRIDGE_URL`; everything else wrote
  `AGENTBOX_MEMORY_BRIDGE`, so it silently reached nothing
- the invite page and oauth-setup declared different Google scopes, which fails
  only when somebody eventually uses the feature
- the speaker service was configured with addresses and no SMTP host, so links
  were minted and never sent, and the page could not say so

A unit test with a fixture on both sides of a seam passes while the seam is
broken, because the fixture *is* the disagreement made invisible. These run
against the real thing.

They skip rather than fail when the system is not up, so CI stays green on a
machine with no containers, and they are honest about being skipped.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GATEWAY = os.environ.get("AGENTBOX_GATEWAY_URL", "http://127.0.0.1:3465")
MEMORY = os.environ.get("AGENTBOX_MEMORY_BRIDGE", "http://127.0.0.1:3471")
ENV_DIR = Path(os.environ.get(
    "AGENTBOX_ENV_DIR", os.path.expanduser("~/.config/agent-control-plane")))


def env_value(service: str, key: str) -> str:
    """Read a deployed value, preferring the running container.

    The env files hold `op://` references rather than literals, so reading the
    file gives you the reference and a 401. The container holds what was
    actually resolved at deploy time, which is the value the system is really
    using — and therefore the one a test about the real system should use.
    """
    container = {
        "memory-bridge": "memory-bridge-memory-bridge-1",
        "agentbox-mcp": "agentbox-mcp-agentbox-mcp-1",
    }.get(service)
    if container:
        result = subprocess.run(["docker", "exec", container, "printenv", key],
                                capture_output=True, text=True, timeout=30,
                                check=False)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    path = ENV_DIR / f"{service}.env"
    if not path.exists():
        return ""
    for line in path.read_text().splitlines():
        if line.startswith(f"{key}="):
            value = line.partition("=")[2].strip().strip("'\"")
            return "" if value.startswith("op://") else value
    return ""


def identity_tokens() -> dict[str, str]:
    raw = env_value("agentbox-mcp", "AGENTBOX_IDENTITIES")
    out = {}
    for pair in raw.split(","):
        name, _, token = pair.partition(":")
        if name.strip() and token.strip():
            out[name.strip()] = token.strip()
    return out


def gateway_up() -> bool:
    try:
        urllib.request.urlopen(f"{GATEWAY}/health", timeout=3).read()
        return True
    except Exception:  # noqa: BLE001
        return False


live = pytest.mark.skipif(not gateway_up(),
                          reason="no running gateway; integration tests need "
                                 "the real system (run cli/agentbox deploy)")


def rpc(method: str, params: dict, token: str, timeout: float = 60) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "method": method, "params": params}).encode()
    request = urllib.request.Request(
        f"{GATEWAY}/mcp", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def call(name: str, args: dict, token: str) -> dict:
    envelope = rpc("tools/call", {"name": name, "arguments": args}, token)
    return envelope.get("result", {})


@pytest.fixture(scope="module")
def tokens():
    found = identity_tokens()
    if not found:
        pytest.skip("no identities configured")
    return found


@pytest.fixture(scope="module")
def alex(tokens):
    return tokens.get("alex") or next(iter(tokens.values()))


# --- the seam that broke first --------------------------------------------------


@live
def test_every_declared_tool_is_callable(alex):
    """A tool can be declared in an integration and have no route behind it.

    Unit tests assert the schema and assert the bridge function separately, and
    both pass while nothing joins them. This calls each read-only tool for real
    and fails on the ones that answer with a transport error rather than a
    result or an honest refusal.
    """
    listed = rpc("tools/list", {}, alex)["result"]["tools"]
    assert listed, "gateway exposed no tools"

    # Read-only, no arguments, safe to call repeatedly.
    harmless = {"list_speakers", "list_home_entities", "list_gmail_labels",
                "list_calendars", "list_projects", "list_memory_proposals",
                "whoami", "list_home_automations"}
    names = {t["name"] for t in listed}
    broken = []
    for name in sorted(harmless & names):
        result = call(name, {}, alex)
        text = json.dumps(result)
        # A policy refusal or an upstream 403 is a working seam. A connection
        # error means the tool points at nothing.
        for symptom in ("unreachable", "Connection refused", "Name or service",
                        "unknown tool", "no route"):
            if symptom in text:
                broken.append(f"{name}: {symptom}")
    assert not broken, f"tools declared but not wired: {broken}"


@live
def test_every_tool_resolves_to_a_policy_capability(alex):
    """Deny-by-default means an unmapped tool is refused, which looks like a
    broken feature rather than a policy decision. Arriving there by forgetting
    to map a tool is the bug."""
    listed = rpc("tools/list", {}, alex)["result"]["tools"]
    policy = (REPO / "policies" / "approval-policy.yaml").read_text()
    mapped = set(re.findall(r"^  (\w+):\s*\w+", policy, re.M))
    unmapped = sorted({t["name"] for t in listed} - mapped)
    assert not unmapped, f"tools with no capability mapping: {unmapped}"


# --- isolation, through the real stack -----------------------------------------


@live
def test_one_identity_cannot_see_another_through_any_tool(tokens):
    """Asserted against the running gateway rather than a fixture.

    The isolation is enforced in three places — the gateway binds identity to
    the session credential, the bridge filters by scope, and the tool schema
    offers no identity argument. A unit test covers one of those at a time.
    """
    if len(tokens) < 2:
        pytest.skip("needs two identities")
    names = sorted(tokens)
    first, second = names[0], names[1]

    for owner, other in ((first, second), (second, first)):
        result = call("list_memory_proposals", {}, tokens[owner])
        payload = result.get("structuredContent", {})
        scopes = {p.get("scope") for p in payload.get("proposals", [])}
        assert other not in scopes, (
            f"{owner}'s session saw {other}-scoped memory")


@live
def test_identity_cannot_be_changed_by_an_argument(tokens):
    """The rule the whole multi-account design rests on: identity comes from
    the session credential, never from anything the model can write."""
    if len(tokens) < 2:
        pytest.skip("needs two identities")
    names = sorted(tokens)
    mine, theirs = names[0], names[1]
    for attempt in ({"identity": theirs}, {"scope": theirs},
                    {"as_identity": theirs}):
        result = call("whoami", attempt, tokens[mine])
        text = json.dumps(result.get("structuredContent", {}))
        assert theirs not in text, f"argument {attempt} changed identity"


# --- configuration agreement ----------------------------------------------------


def test_components_agree_on_the_memory_bridge_variable():
    """The portal read AGENTBOX_MEMORY_BRIDGE_URL while everything else wrote
    AGENTBOX_MEMORY_BRIDGE, so it quietly reached nothing and rendered an
    unreachable service as an empty queue."""
    sources = [REPO / "cli" / "agentbox", REPO / "cli" / "agentbox-portal"]
    used = set()
    for path in sources:
        used.update(re.findall(r'"(AGENTBOX_MEMORY_BRIDGE\w*)"', path.read_text()))
    assert len(used) <= 1, f"components disagree on the variable name: {used}"


def test_google_scopes_match_between_onboarding_and_setup():
    """Two files declare scopes and drift silently, because nothing fails until
    somebody uses the feature and gets an opaque 403 days later."""
    setup = (REPO / "services/compose/google-workspace-bridge"
                    "/oauth-setup.py").read_text()
    invite = (REPO / "cli" / "agentbox-invite").read_text()
    portal = (REPO / "cli" / "agentbox-portal").read_text()
    for scope in ("gmail.modify", "calendar", "drive.file"):
        assert scope in setup and scope in invite and scope in portal, \
            f"{scope} is not declared in all three onboarding paths"


def test_bridge_ports_are_not_published_to_the_host():
    """A bridge token is only unspendable from outside because the bridges have
    no host ports. A published port turns a leaked token into access."""
    for compose in (REPO / "services" / "compose").glob("*/compose.yaml"):
        text = compose.read_text()
        if "agentbox.exposure: lan" in text:
            continue          # Home Assistant, deliberate and labelled
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- ") and re.match(
                    r'- "?\d+:\d+"?$', stripped):
                pytest.fail(f"{compose.parent.name} publishes {stripped}")


# --- round trip -----------------------------------------------------------------


@live
def test_a_proposed_memory_reaches_the_operator_queue(alex):
    """The failure that started all of this: a proposal could be written and
    then be invisible to the only account able to approve it. Write on one
    plane, read on the other."""
    marker = "integration-test marker, safe to reject"
    result = call("propose_memory", {"statement": marker, "kind": "fact"},
                  alex)
    assert "error" not in json.dumps(result).lower()[:200] or \
        result.get("structuredContent"), "proposal was refused"

    token = env_value("memory-bridge", "MEMORY_BRIDGE_TOKEN")
    review = env_value("memory-bridge", "MEMORY_REVIEW_TOKEN")
    if not token:
        pytest.skip("memory bridge token not readable")
    request = urllib.request.Request(
        f"{MEMORY}/v1/proposals?limit=200",
        headers={"Authorization": f"Bearer {token}",
                 "X-Memory-Review-Token": review})
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read())
    matching = [p for p in payload.get("proposals", [])
                if marker in p.get("statement", "")]

    # Clean up before asserting, so a failure does not also leave litter.
    # A test that writes to the live system and walks away turns the operator's
    # review queue into a bin, and the queue is the thing being tested.
    for item in matching:
        try:
            reject = urllib.request.Request(
                f"{MEMORY}/v1/proposals/{item['id']}/reject",
                data=b"{}", method="POST",
                headers={"Authorization": f"Bearer {token}",
                         "X-Memory-Review-Token": review,
                         "Content-Type": "application/json"})
            urllib.request.urlopen(reject, timeout=15).read()
        except (urllib.error.URLError, OSError):
            pass

    assert matching, (
        "a memory the assistant proposed is not visible to the reviewer")


# --- services that must actually be running -------------------------------------


@live
def test_no_service_is_running_stale_source():
    """Catches the failure this is actually for: code committed to git and
    never deployed, so the running container is not what the repo says.

    Deliberately narrower than `doctor` as a whole. A model server that is
    down is a real problem and a real doctor failure, but it is not a reason
    for the *test suite* to go red — a test that fails for environmental
    reasons stops being read.
    """
    result = subprocess.run([str(REPO / "cli" / "agentbox"), "doctor"],
                            capture_output=True, text=True, timeout=300,
                            check=False)
    stale = [line for line in result.stdout.splitlines() if "stale:" in line]
    assert not stale, "services running code that is not in the repo:\n" + \
        "\n".join(stale)


@live
def test_doctor_reports_its_own_health(capsys):
    """Not an assertion on the result — a way to see what doctor thinks
    without the suite hinging on a model server being up."""
    result = subprocess.run([str(REPO / "cli" / "agentbox"), "doctor"],
                            capture_output=True, text=True, timeout=300,
                            check=False)
    failures = [line for line in result.stdout.splitlines()
                if line.startswith("[fail]")]
    if failures:
        print("doctor is unhappy (not failing this test):")
        for line in failures:
            print(" ", line)
