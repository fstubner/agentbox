"""The Operations page's two new jobs: showing the box, and adding a person.

Both replaced something that was not really there — a "Connector health" card
containing only an explanation of why health checks are good, and an onboarding
path that required the admin to open a terminal.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from conftest import code_of
from test_portal_auth import load_portal

REPO = Path(__file__).resolve().parent.parent


def load_status():
    spec = importlib.util.spec_from_file_location(
        "agentbox_status", REPO / "cli" / "agentbox_status.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["agentbox_status"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def status():
    return load_status()


@pytest.fixture
def portal(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOX_POLICY_DIR", str(tmp_path / "policy"))
    return load_portal(tmp_path, monkeypatch)


# --- the status snapshot -------------------------------------------------------


def test_severity_only_applies_when_something_is_wrong(status):
    assert status.Check("x", True, "", status.FAIL).level == status.OK
    assert status.Check("x", False, "", status.FAIL).level == status.FAIL
    assert status.Check("x", False, "", status.WARN).level == status.WARN


def test_a_probe_failure_is_not_an_exception(status):
    """This runs on a page load; an unreachable port must not 500 the page."""
    assert status.probe("http://127.0.0.1:1/nothing", timeout=0.2) is False
    assert status.probe("not-even-a-url", timeout=0.2) is False


def test_docker_being_unavailable_gives_an_empty_list_not_a_crash(status,
                                                                 monkeypatch):
    def explode(*a, **k):
        raise OSError("no docker here")

    monkeypatch.setattr(status.subprocess, "run", explode)
    assert status.services() == []
    assert status.lan_exposure() == []


def test_an_unlabelled_container_is_not_called_stale(status, monkeypatch):
    """It predates source hashing. Calling it stale sends somebody to redeploy
    something that is perfectly current.
    """
    class Result:
        stdout = ("agentbox-mcp-agentbox-mcp-1\tagentbox-mcp\t\t"
                  "Up 2 hours (healthy)\n")

    monkeypatch.setattr(status.subprocess, "run", lambda *a, **k: Result())
    # Looked up by name, not by position: the list also carries services that
    # have no container at all, so a total count is not what this is about.
    found = {s.name: s for s in status.services()}["agentbox-mcp"]
    assert found.running
    assert not found.stale


def test_a_changed_source_hash_is_stale(status, monkeypatch):
    class Result:
        stdout = ("agentbox-mcp-agentbox-mcp-1\tagentbox-mcp\tdeadbeefdeadbeef\t"
                  "Up 2 hours (healthy)\n")

    monkeypatch.setattr(status.subprocess, "run", lambda *a, **k: Result())
    monkeypatch.setattr(status, "source_sha", lambda service: "cafebabecafebabe")
    assert status.services()[0].stale


def test_there_is_exactly_one_definition_of_staleness():
    """cli/agentbox must delegate rather than carry its own copy.

    The first version of the status module wrote a second source_sha that
    hashed git history instead of file contents, and reported every service on
    the box as stale. Two implementations of "is this current" is the bug.
    """
    cli = code_of("cli/agentbox")
    assert "_status().source_sha(service)" in cli
    assert "hashlib.sha256()" not in cli.split("def source_sha")[1][:400]


def test_the_status_module_never_changes_anything(status):
    """Read-only by construction — see its module docstring.

    The portal holds sessions that can edit the admin list. A restart button
    here would make every bug in it considerably more expensive.
    """
    source = code_of("cli/agentbox_status.py")
    for forbidden in ("docker restart", "docker exec", "docker stop",
                      "compose up", "systemctl", "shell=True"):
        assert forbidden not in source, forbidden


def test_snapshot_health_accounts_for_stale_and_stopped(status):
    down = status.Service(name="x", label="X", container="c", running=False,
                          stale=False)
    stale = status.Service(name="y", label="Y", container="c", running=True,
                           stale=True)
    ok = status.Service(name="z", label="Z", container="c", running=True,
                        stale=False)
    assert status.Snapshot(taken_at=0, services=[ok]).healthy
    assert not status.Snapshot(taken_at=0, services=[down]).healthy
    assert not status.Snapshot(taken_at=0, services=[stale]).healthy
    assert not status.Snapshot(
        taken_at=0, other=[status.Check("d", False, "full", status.FAIL)]).healthy


def test_a_model_bound_to_every_interface_is_reported(status, monkeypatch):
    """The model servers ask for no password, so the bind address is the
    whole of their access control.

    Observed live: the main model was on 0.0.0.0 while the other three were on
    127.0.0.1, and after a restart it came back on loopback. It changes across
    restarts, which is exactly the kind of thing nobody notices without a
    check — it is invisible unless somebody thinks to run `ss`.
    """
    class Result:
        stdout = (
            "State  Recv-Q Send-Q Local Address:Port Peer Address:Port\n"
            "LISTEN 0      512          0.0.0.0:1234      0.0.0.0:*\n"
            "LISTEN 0      512        127.0.0.1:1235      0.0.0.0:*\n"
            "LISTEN 0      512        127.0.0.1:1236      0.0.0.0:*\n"
            "LISTEN 0      128          0.0.0.0:22        0.0.0.0:*\n")

    monkeypatch.setattr(status.subprocess, "run", lambda *a, **k: Result())
    found = status.lan_exposure()
    assert [c.name for c in found] == ["main model reachable from the network"]
    # ssh on 0.0.0.0 is deliberate and not ours to complain about.
    assert all("22" not in c.detail for c in found)


def test_loopback_only_is_reported_as_nothing_wrong(status, monkeypatch):
    class Result:
        stdout = ("State Recv-Q Send-Q Local Address:Port Peer\n"
                  "LISTEN 0 512 127.0.0.1:1234 0.0.0.0:*\n")

    monkeypatch.setattr(status.subprocess, "run", lambda *a, **k: Result())
    assert status.lan_exposure() == []


# --- inviting somebody ---------------------------------------------------------


def test_the_assistant_cannot_invite(portal):
    """Creating an identity is granting access to the household."""
    for origin in (portal.ORIGIN_AGENT, portal.ORIGIN_CHAT):
        assert not portal.can(portal.ADMIN, "ops:invite", origin)
    assert portal.can(portal.ADMIN, "ops:invite", portal.ORIGIN_EMAIL)
    assert not portal.can(portal.MEMBER, "ops:invite")
    assert "ops:invite" in portal.AGENT_WITHHELD


def test_an_invite_adds_without_dropping_anybody(portal):
    """The form has two fields and the setting holds everyone.

    Saving the whole map from a two-field form would silently remove every
    person not mentioned in it.
    """
    portal.SETTINGS.save({"identity_emails": "alex:alex@example.com"})
    existing = portal.email_map()
    existing = {a: n for a, n in existing.items() if n != "sam"}
    existing["sam@example.com"] = "sam"
    portal.SETTINGS.save({"identity_emails": ",".join(
        f"{n}:{a}" for a, n in sorted(existing.items()))})
    assert portal.identity_for_email("alex@example.com") == "alex"
    assert portal.identity_for_email("sam@example.com") == "sam"


def test_re_inviting_moves_the_address_rather_than_duplicating(portal):
    """Somebody typing a corrected address should not end up with two."""
    portal.SETTINGS.save({"identity_emails": "sam:old@example.com"})
    existing = {a: n for a, n in portal.email_map().items() if n != "sam"}
    existing["new@example.com"] = "sam"
    portal.SETTINGS.save({"identity_emails": ",".join(
        f"{n}:{a}" for a, n in sorted(existing.items()))})
    assert portal.identity_for_email("old@example.com") == ""
    assert portal.identity_for_email("new@example.com") == "sam"


def test_an_invited_person_is_a_member_not_an_admin(portal):
    """Inviting is not promoting. Admins are a separate, explicit setting."""
    portal.SETTINGS.save({"admins": "alex",
                          "identity_emails": "sam:sam@example.com"})
    assert portal.role_of("sam") == portal.MEMBER
    assert not portal.can(portal.role_of("sam"), "ops:read_health")


def test_a_bad_name_is_refused_before_anything_is_written(portal):
    settings = sys.modules["agentbox_settings"]
    portal.SETTINGS.save({"identity_emails": "alex:alex@example.com"})
    with pytest.raises(settings.InvalidSetting):
        settings.clean_names("Sam Smith")
    assert portal.SETTINGS.value("identity_emails") == "alex:alex@example.com"


def test_an_undeliverable_invite_shows_the_link_rather_than_claiming_success(portal):
    """The alternative is telling somebody an invite was sent when it was not.

    Delivery failure is invisible everywhere else by design — the sign-in page
    must answer identically for registered and unregistered addresses — so
    this is one of the few places it can be said out loud.
    """
    source = code_of("cli/agentbox-portal")
    invite = source.split("def _invite")[1][:2000]
    assert "deliver_link" in invite
    assert "has no way to receive a link yet" in invite


def test_an_invited_link_is_downgraded_because_it_travels(portal):
    """Found by an independent acceptance pass, and the worst thing in it.

    The invite minted ORIGIN_OPERATOR with no browser nonce, so the downgrade
    in redeem_link could never fire — and then emailed it, or spooled it to a
    Discord DM. An operator-privileged link sat in a mailbox the assistant
    holds a read tool for, able to approve memories and disconnect accounts.
    The module docstring states the rule it broke: "Whoever merely reads the
    link cannot use it. That holds for email, for Discord."
    """
    # Behavioural, not a grep. The first version of this test asserted
    # "origin=ORIGIN_CHAT" in a 2600-character slice of the file, which stops
    # guarding the moment the call moves past that offset.
    portal.SETTINGS.save({"smtp_host": "smtp.example.com"})
    assert portal.has_delivery_channel("sam", "sam@example.com") is True
    portal.SETTINGS.save({"smtp_host": ""})
    assert portal.has_delivery_channel("sam", "sam@example.com") is False
    # And the two origins really do differ in what they permit.
    assert portal.can(portal.MEMBER, "memory:decide_own", portal.ORIGIN_OPERATOR)
    assert not portal.can(portal.MEMBER, "memory:decide_own", portal.ORIGIN_CHAT)


def test_a_link_secret_never_reaches_the_log(portal):
    """log_message was overridden and log_request was not, so the stdlib wrote
    the whole request line — 23 live secrets were found in the journal."""
    redact = portal.PortalHandler._redact
    line = redact('"GET /login?id=abc123&k=THE-SECRET HTTP/1.1" 303 -')
    assert "THE-SECRET" not in line and "abc123" not in line
    assert "/login" in line
    # A path with no query is untouched, or the log stops being useful.
    assert redact('"GET /admin HTTP/1.1" 200 -') == '"GET /admin HTTP/1.1" 200 -'


def test_a_stopped_service_turns_red_rather_than_vanishing(status, monkeypatch):
    """`docker ps` without -a derives the list from what is running, so a
    crashed bridge disappears and the page then says everything is running."""
    class Result:
        stdout = ("agentbox-mcp-1\tagentbox-mcp\t\tExited (1) 2 minutes ago\n")

    monkeypatch.setattr(status.subprocess, "run", lambda *a, **k: Result())
    found = {s.name: s for s in status.services()}
    assert found["agentbox-mcp"].running is False
    assert not status.Snapshot(taken_at=0, services=list(found.values())).healthy


def test_a_hand_over_link_keeps_full_privilege(portal):
    """The fix for the mailbox hole took the hand-over path with it.

    With no delivery channel the link is printed on the admin's screen for a
    human to carry — the same act as `agentbox-portal link`, which has never
    been downgraded. Minting ORIGIN_CHAT there left a newly invited member
    unable to approve their own first memory without an operator opening a
    terminal, which is the thing the invite flow exists to avoid.
    """
    portal.SETTINGS.save({"smtp_host": ""})
    travels = portal.has_delivery_channel("sam", "sam@example.com")
    origin = portal.ORIGIN_CHAT if travels else portal.ORIGIN_OPERATOR
    assert origin == portal.ORIGIN_OPERATOR
    assert portal.can(portal.MEMBER, "memory:decide_own", origin)


def test_not_knowing_the_container_state_is_a_finding(status, monkeypatch):
    """`services()` returns [] when docker is unreachable, and all([]) is True
    — so the page said "Everything is running" exactly when it knew least."""
    def explode(*a, **k):
        raise OSError("no docker")

    monkeypatch.setattr(status.subprocess, "run", explode)
    check = status.docker_check()
    assert not check.ok and check.severity == status.FAIL
    assert not status.Snapshot(taken_at=0, services=[], other=[check]).healthy


def test_a_service_nobody_deployed_is_not_a_fault(status):
    """Otherwise the dashboard is permanently red over a service the household
    chose not to run — the failure the scaffold exclusion exists to prevent."""
    up = status.Service(name="a", label="A", container="c", running=True,
                        stale=False)
    never = status.Service(name="b", label="B", container="", running=False,
                           stale=False, deployed=False)
    stopped = status.Service(name="c", label="C", container="c", running=False,
                             stale=False)
    assert status.Snapshot(taken_at=0, services=[up, never]).healthy
    assert not status.Snapshot(taken_at=0, services=[up, stopped]).healthy


def test_one_row_per_service_even_with_an_old_container(status, monkeypatch):
    class Result:
        stdout = ("old-1\tagentbox-mcp\t\tExited (0) 3 days ago\n"
                  "new-1\tagentbox-mcp\t\tUp 2 hours (healthy)\n")

    monkeypatch.setattr(status.subprocess, "run", lambda *a, **k: Result())
    rows = [s for s in status.services() if s.name == "agentbox-mcp"]
    assert len(rows) == 1 and rows[0].running
