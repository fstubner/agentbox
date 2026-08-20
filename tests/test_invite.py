"""Tests for invite-based onboarding.

The invite link is a credential: it authorises creating an identity on someone's
home server, which buys more than a bridge token does. So the tests that matter
are the ones about refusing it — expired, spent, wrong secret, unknown id — and
about the collecting page having no authority of its own.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
from pathlib import Path

import pytest
from conftest import code_of

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
    """A wrong secret and a nonexistent id must be indistinguishable, or the
    page becomes an oracle for which invite ids exist."""
    make(inv)
    _, wrong_secret = inv.valid_invite("abc123", "wrong")
    _, wrong_id = inv.valid_invite("nosuchid", "s3cret")
    assert wrong_secret == wrong_id


def test_an_expired_invite_is_refused(inv):
    make(inv, expires_at=inv.now() - 1)
    record, reason = inv.valid_invite("abc123", "s3cret")
    assert record is None and "expired" in reason


def test_a_spent_invite_is_refused(inv):
    """Single use. Otherwise a forwarded link creates a second identity.

    The refusal is what matters and is unchanged. What it *says* was corrected
    on 2026-08-20: it used to tell them to ask for a new link, when they had
    already done the only thing being asked of them and the next move is the
    admin's. A refusal that misdirects the person reading it produces a second
    credential nobody needed.
    """
    make(inv, used_at=inv.now())
    record, reason = inv.valid_invite("abc123", "s3cret")
    assert record is None
    assert "ask for a new one" not in reason.lower()
    assert "already filled this in" in reason.lower()


def test_the_secret_is_compared_in_constant_time(inv):
    source = code_of(REPO / "cli" / "agentbox-invite")
    assert "hmac.compare_digest" in source
    assert "record.get(\"secret\"" in source


def test_the_saved_invite_is_not_world_readable(inv, tmp_path):
    make(inv)
    mode = (tmp_path / "abc123.json").stat().st_mode & 0o777
    assert mode == 0o600


# --- the page has no authority -----------------------------------------------------


def test_the_page_cannot_provision_anything(inv):
    """The collecting half must not hold the privileges. A LAN-reachable page
    with a docker socket would be the worst service on the box — `docker
    inspect` reads every bridge credential."""
    import ast
    source = code_of(REPO / "cli" / "agentbox-invite")
    tree = ast.parse(source)

    # Imported modules, not source text — the module docstring legitimately
    # explains at length why docker is absent, and an earlier version of this
    # test tripped over its own explanation.
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for forbidden in ("subprocess", "shutil", "ctypes"):
        assert forbidden not in imported, f"imports {forbidden}"

    # And no credential names anywhere outside comments.
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    code = code.split('"""', 2)[-1]          # drop the module docstring
    for forbidden in ("OP_SERVICE_ACCOUNT", "BRIDGE_TOKEN", "docker exec"):
        assert forbidden not in code, forbidden


def test_the_page_never_logs_the_secret(inv):
    """The invite secret travels in the query string, which is exactly what a
    default HTTP log line prints."""
    source = code_of(REPO / "cli" / "agentbox-invite")
    assert "def log_message" in source
    block = source.split("def log_message", 1)[1].split("\n    def ", 1)[0]
    assert "urlparse(self.path).path" in block


def test_shared_services_are_stated_not_offered(inv):
    """One house has one Home Assistant. Offering it as a choice would imply a
    decision that does not exist."""
    assert inv.CONNECTORS["homeassistant"]["personal"] is False
    assert inv.CONNECTORS["memory"]["personal"] is False
    assert inv.CONNECTORS["google"]["personal"] is True
    assert inv.CONNECTORS["vikunja"]["personal"] is True


def test_google_is_marked_external(inv):
    """The one thing we cannot provision for her — she must consent herself."""
    assert inv.CONNECTORS["google"]["external"] is True
    assert inv.CONNECTORS["vikunja"]["external"] is False


def test_only_personal_connectors_can_be_chosen(inv):
    """A submitted form naming a shared service must not create a per-identity
    anything."""
    source = code_of(REPO / "cli" / "agentbox-invite")
    assert 'CONNECTORS[c]["personal"]' in source


# --- the operator half ---------------------------------------------------------------


def test_completion_requires_a_submitted_invite():
    source = code_of(REPO / "cli" / "agentbox")
    block = source.split("def invite_complete", 1)[1].split("\ndef ", 1)[0]
    assert 'record.get("used_at")' in block
    assert 'record.get("completed_at")' in block


def test_completion_drops_the_secret_when_done():
    """Spent credentials should not linger on disk."""
    source = code_of(REPO / "cli" / "agentbox")
    block = source.split("def invite_complete", 1)[1].split("\ndef ", 1)[0]
    assert 'record.pop("secret", None)' in block


def test_vikunja_provisioning_uses_the_container_cli():
    """Registration is disabled on this deployment, so the HTTP API cannot
    create her account — and that privilege is why the web page has none."""
    source = code_of(REPO / "cli" / "agentbox")
    block = source.split("def provision_vikunja_user", 1)[1].split("\ndef ", 1)[0]
    assert '"docker", "exec"' in block
    assert '"user", "create"' in block


def test_consent_is_the_only_part_that_is_not_automated():
    """Superseded an earlier version that printed OAuth steps as homework.

    We can neither create a Google account nor consent on her behalf — but
    everything *after* consent is ours to do, and printing it as instructions
    was stopping at the hard part. She consents in the page; the exchange and
    the bridge are automatic."""
    source = code_of(REPO / "cli" / "agentbox")
    block = source.split("def invite_complete", 1)[1].split("\ndef ", 1)[0]
    assert "exchange_oauth_code" in block
    assert "provision_google_bridge" in block
    # And the old homework is gone.
    assert "Run the OAuth flow signed in as HER" not in source


# --- automatic connector provisioning -------------------------------------------
#
# "She picks Gmail and it works" is the whole point of onboarding. The parts
# that can be automated now are; the one that cannot — her consent — happens in
# the page rather than being printed as homework.


def test_the_page_can_start_google_consent_but_not_finish_it(inv):
    """The privilege split, stated in code: a client id is public and lives
    here, a client secret is not and does not. So the page can send her to
    Google and receive a code, and a code without the secret is inert."""
    source = code_of(REPO / "cli" / "agentbox-invite")
    assert "AGENTBOX_GOOGLE_CLIENT_ID" in source
    assert "CLIENT_SECRET" not in source
    assert "oauth2.googleapis.com/token" not in source  # no exchange here


def test_consent_asks_for_a_refresh_token_explicitly(inv):
    """access_type=offline plus prompt=consent are what make Google return a
    refresh token. Without them a returning user gets none, and the failure
    surfaces minutes later on the operator side instead of here."""
    source = code_of(REPO / "cli" / "agentbox-invite")
    block = source.split("def google_auth_url", 1)[1].split("\ndef ", 1)[0]
    assert '"access_type": "offline"' in block
    assert '"prompt": "consent"' in block


def test_the_callback_verifies_state_against_the_invite(inv):
    """`state` carries the invite through Google and back. If it were not
    checked, anyone could post a code and have a bridge provisioned."""
    source = code_of(REPO / "cli" / "agentbox-invite")
    block = source.split("def _google_callback", 1)[1].split("\n    def ", 1)[0]
    assert "hmac.compare_digest" in block


def test_the_exchange_lives_on_the_operator_side():
    source = code_of(REPO / "cli" / "agentbox")
    assert "oauth2.googleapis.com/token" in source
    block = source.split("def exchange_oauth_code", 1)[1].split("\ndef ", 1)[0]
    assert "GOOGLE_CLIENT_SECRET" in block


def test_a_provisioned_bridge_holds_only_that_persons_token():
    """The reason for a second container rather than a second credential in the
    first one."""
    source = code_of(REPO / "cli" / "agentbox")
    block = source.split("def provision_google_bridge", 1)[1].split("\ndef ", 1)[0]
    assert "GOOGLE_REFRESH_TOKEN={refresh_token}" in block
    # A fresh bridge token per identity, not the shared one.
    assert "_secrets.token_hex(32)" in block


def test_a_new_identity_gets_no_writable_calendar():
    """Inheriting the operator's would let her assistant write to his."""
    source = code_of(REPO / "cli" / "agentbox")
    block = source.split("def provision_google_bridge", 1)[1].split("\ndef ", 1)[0]
    assert '"GOOGLE_ALLOWED_WRITE_CALENDAR_ID="' in block


def test_the_identity_bridge_joins_the_existing_network():
    """A per-identity network would mean editing the gateway's compose every
    time somebody joins — and a config the onboarding flow rewrites is one that
    will eventually be rewritten wrongly."""
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
    """If she skipped the Google step she falls back to the shared bridge —
    correct for shared services, wrong for mail, so it must be said."""
    source = code_of(REPO / "cli" / "agentbox")
    block = source.split("def invite_complete", 1)[1].split("\ndef ", 1)[0]
    assert "did not finish the consent" in block
    assert "wrong for mail" in block
