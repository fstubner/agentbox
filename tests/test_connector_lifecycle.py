"""Reconnecting and disconnecting an identity's Google account.

The flow spans two processes on purpose — a LAN-reachable page must not hold
the credential that mints refresh tokens — so these test both halves and, in
particular, the seam between them.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    loader = importlib.machinery.SourceFileLoader(name, str(REPO / "cli" / filename))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def portal(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOX_PORTAL_DIR", str(tmp_path / "portal"))
    monkeypatch.setenv("AGENTBOX_ADMINS", "alex")
    monkeypatch.setenv("AGENTBOX_GOOGLE_CLIENT_ID", "client-123.apps")
    monkeypatch.setenv("AGENTBOX_ENV_DIR", str(tmp_path / "env"))
    (tmp_path / "env").mkdir(parents=True, exist_ok=True)
    return _load("agentbox_portal", "agentbox-portal")


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOX_PORTAL_DIR", str(tmp_path / "portal"))
    monkeypatch.setenv("AGENTBOX_ENV_DIR", str(tmp_path / "env"))
    (tmp_path / "env").mkdir(parents=True, exist_ok=True)
    return _load("agentbox_cli", "agentbox")


# --- consent binding -----------------------------------------------------------


def test_consent_state_is_bound_to_the_identity(portal):
    """A crafted callback must not land one person's code in another's record.

    Without this, whoever's Google account Agentbox ends up reading would be
    the attacker's choice rather than the user's.
    """
    assert portal._consent_state("alex") != portal._consent_state("sam")
    assert portal._consent_state("alex") == portal._consent_state("alex")


def test_consent_state_survives_a_restart(portal, tmp_path, monkeypatch):
    """Derived, not stored: a restart between consent and callback would
    otherwise strand the user mid-flow with no way back."""
    first = portal._consent_state("sam")
    reloaded = _load("agentbox_portal2", "agentbox-portal")
    assert reloaded._consent_state("sam") == first


def test_consent_url_forces_a_fresh_refresh_token(portal):
    """Google issues a refresh token on first grant only.

    A re-consent without prompt=consent returns none, so the flow would report
    success and change nothing — the exact silent no-op this whole piece exists
    to remove.
    """
    url = portal.google_consent_url("state123")
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "state=state123" in url


def test_consent_url_carries_every_scope_the_bridge_needs(portal):
    url = portal.google_consent_url("s")
    for scope in ("gmail.modify", "calendar", "drive.file",
                  "drive.activity.readonly", "contacts.readonly"):
        assert scope in url


# --- the spool -----------------------------------------------------------------


def test_request_is_written_for_the_operator(portal):
    portal.save_request({"id": "r1", "identity": "sam", "connector": "google",
                         "action": "reconnect", "code": "auth-code",
                         "created_at": portal.now(), "completed_at": None})
    pending = portal.pending_requests("sam")
    assert len(pending) == 1
    assert pending[0]["action"] == "reconnect"
    assert portal.pending_requests("alex") == []


def test_authorisation_code_is_not_world_readable(portal):
    portal.save_request({"id": "r2", "identity": "sam", "code": "secret-code",
                         "action": "reconnect", "created_at": portal.now(),
                         "completed_at": None})
    mode = portal.request_path("r2").stat().st_mode & 0o777
    assert mode == 0o600


def test_completed_requests_stop_showing(portal):
    portal.save_request({"id": "r3", "identity": "sam", "action": "reconnect",
                         "code": "c", "created_at": portal.now(),
                         "completed_at": portal.now()})
    assert portal.pending_requests("sam") == []


def test_the_portal_cannot_exchange_a_code_itself(portal):
    """The split is the point: this half has no client secret and no route to
    one. If it could exchange, a LAN page would mint refresh tokens."""
    source = (REPO / "cli" / "agentbox-portal").read_text()
    assert "CLIENT_SECRET" not in source
    assert "oauth2.googleapis.com/token" not in source


# --- the operator half ---------------------------------------------------------


def test_cli_reads_what_the_portal_wrote(portal, cli):
    """Both halves must agree on the spool location and shape, or the flow
    silently does nothing."""
    portal.save_request({"id": "r4", "identity": "sam", "connector": "google",
                         "action": "reconnect", "code": "auth-code",
                         "created_at": portal.now(), "completed_at": None})
    found = cli.portal_requests("sam")
    assert len(found) == 1
    assert found[0]["code"] == "auth-code"


def test_expired_authorisation_codes_are_refused(portal, cli, capsys):
    """Google expires codes at about ten minutes. Exchanging a stale one fails
    upstream with an opaque error; saying so plainly is kinder and cheaper."""
    portal.save_request({"id": "r5", "identity": "sam", "connector": "google",
                         "action": "reconnect", "code": "old",
                         "created_at": int(time.time()) - 3600,
                         "completed_at": None})
    assert cli.identity_reconnect("sam") == 1
    assert "minutes old" in capsys.readouterr().out


def test_spent_code_is_removed_from_disk(portal, cli):
    record = {"id": "r6", "identity": "sam", "action": "reconnect",
              "code": "auth-code", "created_at": portal.now(),
              "completed_at": None,
              "_path": str(portal.request_path("r6"))}
    portal.save_request({k: v for k, v in record.items() if k != "_path"})
    cli._mark_request_done(dict(record))
    on_disk = json.loads(portal.request_path("r6").read_text())
    assert "code" not in on_disk
    assert on_disk["completed_at"]


def test_reconnect_without_a_request_does_nothing(cli, capsys):
    assert cli.identity_reconnect("sam") == 1
    assert "no pending reconnect" in capsys.readouterr().out


# --- disconnect ----------------------------------------------------------------


def test_disconnect_revokes_upstream_not_just_locally(cli, tmp_path, monkeypatch):
    """Deleting our copy is not disconnecting.

    The grant would still be listed in the person's Google account, and anyone
    who had captured the token could still spend it.
    """
    env = tmp_path / "env" / "sam-google-bridge.env"
    env.write_text("GOOGLE_REFRESH_TOKEN=the-token\n")
    revoked = {}
    monkeypatch.setattr(cli, "revoke_google_token",
                        lambda token: revoked.setdefault("token", token) or True)
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(cli, "write_env_value", lambda *a, **k: None)
    cli.identity_disconnect("sam")
    assert revoked["token"] == "the-token"
    assert not env.exists()


def test_already_dead_token_counts_as_revoked(cli, monkeypatch):
    """Google answers 400 invalid_token for a credential that is already
    dead, which is the end state we wanted."""
    import urllib.error

    def boom(*args, **kwargs):
        raise urllib.error.HTTPError("u", 400, "invalid_token", {}, None)

    monkeypatch.setattr(cli.urllib.request, "urlopen", boom)
    assert cli.revoke_google_token("dead") is True


def test_revocation_failure_is_reported_not_swallowed(cli, tmp_path,
                                                      monkeypatch, capsys):
    """If we cannot confirm revocation the person must be told to check by
    hand, not left believing access ended."""
    env = tmp_path / "env" / "sam-google-bridge.env"
    env.write_text("GOOGLE_REFRESH_TOKEN=the-token\n")
    monkeypatch.setattr(cli, "revoke_google_token", lambda token: False)
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(cli, "write_env_value", lambda *a, **k: None)
    cli.identity_disconnect("sam")
    assert "check the account's connected apps" in capsys.readouterr().out


def test_disconnect_without_a_connection_is_refused(cli, capsys):
    assert cli.identity_disconnect("nobody") == 1
    assert "nothing to" in capsys.readouterr().out


# --- dispatch safety -----------------------------------------------------------


def test_unknown_identity_subcommand_does_not_delete(cli):
    """`remove` used to be the fallthrough, so any subcommand added without a
    branch would silently delete an identity instead of erroring."""
    source = (REPO / "cli" / "agentbox").read_text()
    block = source.split('if args.cmd == "identity":')[1][:900]
    assert 'if args.identity_cmd == "remove":' in block
    remove_calls = block.count("identity_remove(")
    assert remove_calls == 1
