"""Tests for invite-based onboarding.

An invite link authorises creating an identity on someone's home server, which
is more access than a bridge token gives. So most tests check that it is refused when
expired, used, wrong or unknown, and that the collecting page has no authority
of its own.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
from pathlib import Path

import pytest
from conftest import code_of, script_code

REPO = Path(__file__).resolve().parent.parent


def load_invite_module(tmp_path):
    """The CLI has no .py extension, so importlib needs an explicit loader."""
    os.environ["AGENTBOX_INVITE_DIR"] = str(tmp_path)
    name = f"inv_{abs(hash(str(tmp_path)))}"
    path = REPO / "cli" / "agentbox-invite"
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def inv(tmp_path):
    return load_invite_module(tmp_path)


def make(inv, **overrides):
    record = {"id": "abc123", "secret": "s3cret", "identity": "sam",
              "created_at": inv.now(), "expires_at": inv.now() + 3600,
              "used_at": None}
    record.update(overrides)
    inv.save_invite(record)
    return record


# --- the link is a credential ----------------------------------------------------


def test_a_valid_invite_is_accepted(inv):
    make(inv)
    record, reason = inv.valid_invite("abc123", "s3cret")
    assert record and not reason


def test_a_wrong_secret_is_refused(inv):
    make(inv)
    record, reason = inv.valid_invite("abc123", "wrong")
    assert record is None


def test_an_unknown_invite_is_refused(inv):
    record, reason = inv.valid_invite("nope", "s3cret")
    assert record is None


def test_refusals_do_not_say_which_part_was_wrong(inv):
    """A wrong secret and a nonexistent id must get the same answer, or the
    page would reveal which invite ids exist."""
    make(inv)
    _, wrong_secret = inv.valid_invite("abc123", "wrong")
    _, wrong_id = inv.valid_invite("nosuchid", "s3cret")
    assert wrong_secret == wrong_id


def test_an_expired_invite_is_refused(inv):
    make(inv, expires_at=inv.now() - 1)
    record, reason = inv.valid_invite("abc123", "s3cret")
    assert record is None and "expired" in reason


def test_a_spent_invite_is_refused(inv):
    """Single use, or a forwarded link would create a second identity.

    The message must not tell them to ask for a new link. They have already
    done the one thing asked of them, and the next step is the admin's.
    """
    make(inv, used_at=inv.now())
    record, reason = inv.valid_invite("abc123", "s3cret")
    assert record is None
    assert "ask for a new one" not in reason.lower()
    assert "already filled this in" in reason.lower()


def test_the_secret_is_compared_in_constant_time(inv):
    source = script_code("agentbox-invite")
    assert "hmac.compare_digest" in source
    assert "record.get(\"secret\"" in source


def test_the_saved_invite_is_not_world_readable(inv, tmp_path):
    make(inv)
    mode = (tmp_path / "abc123.json").stat().st_mode & 0o777
    assert mode == 0o600


# --- the page has no authority -----------------------------------------------------


def test_the_page_cannot_provision_anything(inv):
    """The collecting half must not hold the privileges. A web page on the LAN
    with the Docker socket would be the worst service on the box, since `docker
    inspect` reads every bridge credential."""
    import ast
    source = script_code("agentbox-invite")
    tree = ast.parse(source)

    # Imported modules, not source text, because the module docstring explains
    # why Docker is absent.
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for forbidden in ("subprocess", "shutil", "ctypes"):
        assert forbidden not in imported, f"imports {forbidden}"

    # No credential names anywhere outside comments.
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    code = code.split('"""', 2)[-1]          # drop the module docstring
    for forbidden in ("OP_SERVICE_ACCOUNT", "BRIDGE_TOKEN", "docker exec"):
        assert forbidden not in code, forbidden


def test_the_page_never_logs_the_secret(inv):
    """The invite secret travels in the query string, which a default HTTP
    log line prints."""
    source = script_code("agentbox-invite")
    assert "def log_message" in source
    block = source.split("def log_message", 1)[1].split("\n    def ", 1)[0]
    assert "urlparse(self.path).path" in block


def test_shared_services_are_stated_not_offered(inv):
    """One house has one Home Assistant, so there is nothing to choose and it
    is not offered as a choice."""
    assert inv.CONNECTORS["homeassistant"]["personal"] is False
    assert inv.CONNECTORS["memory"]["personal"] is False
    assert inv.CONNECTORS["google"]["personal"] is True
    assert inv.CONNECTORS["vikunja"]["personal"] is True


def test_google_is_marked_external(inv):
    """Nobody can set this up for the person, because they must consent."""
    assert inv.CONNECTORS["google"]["external"] is True
    assert inv.CONNECTORS["vikunja"]["external"] is False


def test_only_personal_connectors_can_be_chosen(inv):
    """A submitted form naming a shared service must not create anything per
    identity."""
    source = script_code("agentbox-invite")
    assert 'CONNECTORS[c]["personal"]' in source


# --- the operator half ---------------------------------------------------------------


def test_completion_requires_a_submitted_invite():
    source = code_of(REPO / "cli" / "agentbox_invites.py")
    block = source.split("def invite_complete", 1)[1].split("\ndef ", 1)[0]
    assert 'record.get("used_at")' in block
    assert 'record.get("completed_at")' in block


def test_completion_drops_the_secret_when_done():
    """Spent credentials should not linger on disk."""
    source = code_of(REPO / "cli" / "agentbox_invites.py")
    block = source.split("def invite_complete", 1)[1].split("\ndef ", 1)[0]
    assert 'record.pop("secret", None)' in block


def test_vikunja_provisioning_uses_the_container_cli():
    """Registration is off, so the HTTP API cannot create the account, and that
    privilege is why the web page has none."""
    source = code_of(REPO / "cli" / "agentbox_invites.py")
    block = source.split("def provision_vikunja_user", 1)[1].split("\ndef ", 1)[0]
    assert '"docker", "exec"' in block
    assert '"user", "create"' in block


def test_consent_is_the_only_part_that_is_not_automated():
    """Nobody else can create a Google account or consent for the person, but
    everything after consent is automatic. They consent in the page, and the
    exchange and the bridge follow."""
    source = code_of(REPO / "cli" / "agentbox_invites.py")
    block = source.split("def invite_complete", 1)[1].split("\ndef ", 1)[0]
    assert "exchange_oauth_code" in block
    assert "provision_google_bridge" in block
    # The manual OAuth instructions are gone.
    assert "Run the OAuth flow signed in as HER" not in source


# --- automatic connector provisioning -------------------------------------------
#
# Picking Gmail during onboarding should be enough to make it work. Everything
# that can be automated is, and consent happens in the page.


def test_the_page_can_start_google_consent_but_not_finish_it(inv):
    """A client id is public and lives here. A client secret is not public
    and does not. So the page can send the person to Google and receive a
    code, and a code without the secret cannot be used."""
    source = script_code("agentbox-invite")
    assert "AGENTBOX_GOOGLE_CLIENT_ID" in source
    assert "CLIENT_SECRET" not in source
    assert "oauth2.googleapis.com/token" not in source  # no exchange here


def test_consent_asks_for_a_refresh_token_explicitly(inv):
    """access_type=offline plus prompt=consent are what make Google return a
    refresh token. Without them a returning user gets none, and the failure
    shows up minutes later on the operator side instead of here."""
    source = script_code("agentbox-invite")
    block = source.split("def google_auth_url", 1)[1].split("\ndef ", 1)[0]
    assert '"access_type": "offline"' in block
    assert '"prompt": "consent"' in block


def test_the_callback_verifies_state_against_the_invite(inv):
    """`state` carries the invite through Google and back. If it were not
    checked, anyone could post a code and have a bridge provisioned."""
    source = script_code("agentbox-invite")
    block = source.split("def _google_callback", 1)[1].split("\n    def ", 1)[0]
    assert "hmac.compare_digest" in block


def test_the_exchange_lives_on_the_operator_side():
    source = code_of(REPO / "cli" / "agentbox_accounts.py")
    assert "oauth2.googleapis.com/token" in source
    block = source.split("def exchange_oauth_code", 1)[1].split("\ndef ", 1)[0]
    assert "GOOGLE_CLIENT_SECRET" in block


def test_a_provisioned_bridge_holds_only_that_persons_token():
    """Each person gets a second container, not a second credential in the
    first one, so a bridge holds one person's token."""
    source = code_of(REPO / "cli" / "agentbox_accounts.py")
    block = source.split("def provision_google_bridge", 1)[1].split("\ndef ", 1)[0]
    assert "GOOGLE_REFRESH_TOKEN={refresh_token}" in block
    # A fresh bridge token per identity, not the shared one.
    assert "_secrets.token_hex(32)" in block


def test_a_new_identity_gets_no_writable_calendar():
    """Inheriting the operator's would let the newcomer write to that one."""
    source = code_of(REPO / "cli" / "agentbox_accounts.py")
    block = source.split("def provision_google_bridge", 1)[1].split("\ndef ", 1)[0]
    assert '"GOOGLE_ALLOWED_WRITE_CALENDAR_ID="' in block


def test_the_identity_bridge_joins_the_existing_network():
    """A network per person would mean editing the gateway's compose file every
    time someone joins. A file the onboarding flow rewrites is likely to be
    rewritten wrongly at some point."""
    compose = (REPO / "services" / "compose" / "google-workspace-bridge"
               / "identity.compose.yaml").read_text()
    assert "google-workspace-bridge_default" in compose
    assert "external: true" in compose


def test_the_identity_bridge_publishes_no_host_port():
    compose = (REPO / "services" / "compose" / "google-workspace-bridge"
               / "identity.compose.yaml").read_text()
    assert "ports:" not in compose
    assert "agentbox.exposure: private" in compose


def test_missing_consent_is_reported_rather_than_silently_shared():
    """Skipping the Google step falls back to the shared bridge, which is right
    for shared services and wrong for mail, so the CLI says so."""
    source = code_of(REPO / "cli" / "agentbox_invites.py")
    block = source.split("def invite_complete", 1)[1].split("\ndef ", 1)[0]
    assert "did not finish the consent" in block
    assert "wrong for mail" in block
