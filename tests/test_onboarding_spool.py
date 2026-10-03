"""The portal asks for an onboarding, and a privileged worker decides.

Completing an invite starts a new bridge with `docker compose up`, a privilege
kept off the web page. The portal only triggers it.

These tests check that a request decides only which invite is meant. The
worker re-derives everything else from the invite record, so false values in
any other field of a request change nothing.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
import time

import pytest
from conftest import REPO_ROOT as REPO
from conftest import portal_code

GOOD = "0123456789abcdef"          # secrets.token_hex(8) shape
OTHER = "fedcba9876543210"


def load(name: str, filename: str, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOX_ONBOARDING_DIR", str(tmp_path / "onboarding"))
    monkeypatch.setenv("AGENTBOX_INVITE_DIR", str(tmp_path / "invites"))
    monkeypatch.setenv("AGENTBOX_PORTAL_DIR", str(tmp_path / "portal"))
    monkeypatch.setenv("AGENTBOX_ADMINS", "alex")
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(
            name, str(REPO / "cli" / filename)))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def spool(tmp_path, monkeypatch):
    return load("agentbox_onboarding", "agentbox_onboarding.py",
                tmp_path, monkeypatch)


@pytest.fixture
def cli(tmp_path, monkeypatch, spool):
    module = load("agentbox_invites_under_test", "agentbox_invites.py", tmp_path, monkeypatch)
    (tmp_path / "invites").mkdir(exist_ok=True)
    return module


def write_invite(tmp_path, token_id=GOOD, **over):
    record = {"id": token_id, "identity": "sam", "display_name": "Sam",
              "secret": "s3cret-do-not-render", "connectors": ["google"],
              "used_at": 1000, "expires_at": 9_999_999_999}
    record.update(over)
    path = tmp_path / "invites" / f"{token_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return record


# --- a request carries only an invite id --------------------------------------


def test_an_id_that_is_not_an_id_is_refused(spool):
    """It arrives from a form and becomes a filename."""
    for bad in ("../../etc/passwd", "a/b", "", "x" * 17, GOOD + "0",
                "0123456789ABCDEF", "id with space"):
        with pytest.raises(ValueError):
            spool.request_path(bad)
    assert spool.request_path(GOOD).name == f"{GOOD}.json"


def test_a_request_filed_under_another_name_is_ignored(spool, tmp_path):
    """A request whose field names one invite and whose filename names another.

    Trusting the field alone would let a request for an invite the approver
    never saw pass under the filename of one they did.
    """
    directory = tmp_path / "onboarding"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{GOOD}.json").write_text(
        json.dumps({"token_id": OTHER, "requested_at": int(time.time())}),
        encoding="utf-8")
    assert spool.pending() == []


def test_one_unreadable_request_does_not_stop_the_others(spool, tmp_path):
    directory = tmp_path / "onboarding"
    spool.request(GOOD, "alex")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{OTHER}.json").write_text("{not json", encoding="utf-8")
    assert [r["token_id"] for r in spool.pending()] == [GOOD]


def test_settled_requests_do_not_look_pending(spool):
    """The path unit depends on this.

    Requests settle into a subdirectory and are not deleted, so the spool
    directory is permanently non-empty once anything has been approved. If
    settled records read as pending, every drain would find work forever.
    """
    spool.request(GOOD, "alex")
    [record] = spool.pending()
    spool.settle(record, "completed")
    assert spool.pending() == []
    assert (spool.settled_dir() / f"{GOOD}.json").exists()


def test_an_approval_is_a_decision_with_a_shelf_life(spool):
    spool.request(GOOD, "alex")
    [record] = spool.pending()
    assert not spool.expired(record)
    assert spool.expired(record, now=int(time.time()) + spool.MAX_REQUEST_AGE + 1)


# --- the worker re-derives everything -----------------------------------------


def drain(cli, monkeypatch):
    """Run the worker with the privileged step stubbed out."""
    calls = []

    def fake(token_id):
        calls.append(token_id)
        return fake.code

    fake.code = 0
    monkeypatch.setattr(cli, "invite_complete", fake)
    code = cli.invite_drain()
    return calls, code, fake


def test_a_request_cannot_invent_an_onboarding(cli, spool, monkeypatch):
    """A request cannot create an identity. At most it finishes an
    onboarding a human already started."""
    spool.request(GOOD, "alex")
    calls, _, _ = drain(cli, monkeypatch)
    assert calls == []
    settled = json.loads(
        (spool.settled_dir() / f"{GOOD}.json").read_text(encoding="utf-8"))
    assert settled["outcome"] == "unknown"


def test_an_invite_nobody_filled_in_is_not_completed(cli, spool, tmp_path,
                                                     monkeypatch):
    write_invite(tmp_path, used_at=0)
    spool.request(GOOD, "alex")
    calls, _, _ = drain(cli, monkeypatch)
    assert calls == []


def test_an_already_completed_invite_settles_quietly(cli, spool, tmp_path,
                                                     monkeypatch):
    """Two admins approving the same person should give one result and no
    error."""
    write_invite(tmp_path, completed_at=1234)
    spool.request(GOOD, "alex")
    calls, code, _ = drain(cli, monkeypatch)
    assert calls == [] and code == 0


def test_the_approver_is_recorded_but_never_consulted(cli, spool, tmp_path,
                                                      monkeypatch):
    """The approver and origin are recorded for auditing, not trusted.

    A request with a nonsense approver still completes a valid invite, because
    the worker reads the invite. The reverse matters more. A plausible
    approver on a bogus invite completes nothing.
    """
    write_invite(tmp_path)
    spool.request(GOOD, requested_by="", origin="agent")
    calls, code, _ = drain(cli, monkeypatch)
    assert calls == [GOOD] and code == 0
    settled = json.loads(
        (spool.settled_dir() / f"{GOOD}.json").read_text(encoding="utf-8"))
    assert settled["outcome"] == "completed"


def test_a_failure_settles_instead_of_retrying(cli, spool, tmp_path,
                                               monkeypatch):
    """An unattended worker that leaves failures in the spool would loop.

    The path unit re-arms when the spool empties, so a request left behind on
    failure would fire again each time anything else was approved.
    """
    write_invite(tmp_path)
    spool.request(GOOD, "alex")

    calls = []

    def failing(token_id):
        calls.append(token_id)
        return 1

    monkeypatch.setattr(cli, "invite_complete", failing)
    assert cli.invite_drain() == 1
    assert calls == [GOOD]
    # Reported as a failure, and still removed from the spool.
    assert spool.pending() == []
    settled = json.loads(
        (spool.settled_dir() / f"{GOOD}.json").read_text(encoding="utf-8"))
    assert settled["outcome"] == "failed"
    # A second trigger finds nothing to do and does not retry.
    assert cli.invite_drain() == 0
    assert calls == [GOOD]


def test_an_expired_approval_completes_nothing(cli, spool, tmp_path,
                                               monkeypatch):
    write_invite(tmp_path)
    path = spool.request(GOOD, "alex")
    stale = json.loads(path.read_text(encoding="utf-8"))
    stale["requested_at"] = int(time.time()) - spool.MAX_REQUEST_AGE - 60
    path.write_text(json.dumps(stale), encoding="utf-8")
    calls, _, _ = drain(cli, monkeypatch)
    assert calls == []


# --- the portal side ----------------------------------------------------------


@pytest.fixture
def portal(tmp_path, monkeypatch, spool):
    module = load("agentbox_portal", "agentbox-portal", tmp_path, monkeypatch)
    (tmp_path / "invites").mkdir(exist_ok=True)
    return module


def test_the_assistant_cannot_approve_an_onboarding(portal):
    """Completing an onboarding decides who lives here. It is withheld from
    every downgraded origin under the same capability as inviting."""
    for origin in (portal.ORIGIN_AGENT, portal.ORIGIN_CHAT):
        assert not portal.can(portal.ADMIN, "ops:invite", origin), origin


def test_the_card_does_not_carry_the_invite_secret(portal, tmp_path):
    """The secret would let somebody impersonate the invitee."""
    write_invite(tmp_path)
    assert "s3cret-do-not-render" not in portal.render_onboarding_card()
    assert GOOD in portal.render_onboarding_card()


def test_a_completed_invite_leaves_the_waiting_list(portal, tmp_path):
    write_invite(tmp_path, completed_at=99)
    assert portal.render_onboarding_card() == ""


def test_the_card_warns_when_the_google_step_was_skipped(portal, tmp_path):
    """Completing without Google consent leaves the person on the shared
    bridge, which is wrong for mail, so the card says so before the click."""
    write_invite(tmp_path, google_code="")
    assert "consent" in portal.render_onboarding_card().lower()
    write_invite(tmp_path, google_code="4/abc")
    assert "consent" not in portal.render_onboarding_card().lower()


def test_the_portal_asks_and_does_not_act(portal):
    """This is why the work is split. Nothing on this path may run docker."""
    body = portal_code().split("def _onboard")[1][:2000]
    assert "agentbox_onboarding.request" in body
    for forbidden in ("docker", "subprocess", "invite_complete", "compose"):
        assert forbidden not in body, forbidden


# --- the trigger --------------------------------------------------------------


def test_the_path_unit_cannot_latch_on_the_settled_directory():
    """DirectoryNotEmpty would stay true after the first onboarding, because
    settled records live inside the directory being watched."""
    unit = (REPO / "cli" / "agentbox-onboarding.path").read_text(
        encoding="utf-8")
    body = "\n".join(line for line in unit.splitlines()
                     if not line.lstrip().startswith("#"))
    assert "PathExistsGlob=" in body and "*.json" in body
    assert "DirectoryNotEmpty" not in body
    assert "Unit=agentbox-onboarding.service" in body
