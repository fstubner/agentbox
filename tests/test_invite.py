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
    """Single use. Otherwise a forwarded link creates a second identity."""
    make(inv, used_at=inv.now())
    record, reason = inv.valid_invite("abc123", "s3cret")
    assert record is None and "already been used" in reason


def test_the_secret_is_compared_in_constant_time(inv):
    source = (REPO / "cli" / "agentbox-invite").read_text()
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
    source = (REPO / "cli" / "agentbox-invite").read_text()
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
    source = (REPO / "cli" / "agentbox-invite").read_text()
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
    source = (REPO / "cli" / "agentbox-invite").read_text()
    assert 'CONNECTORS[c]["personal"]' in source


# --- the operator half ---------------------------------------------------------------


def test_completion_requires_a_submitted_invite():
    source = (REPO / "cli" / "agentbox").read_text()
    block = source.split("def invite_complete", 1)[1].split("\ndef ", 1)[0]
    assert 'record.get("used_at")' in block
    assert 'record.get("completed_at")' in block


def test_completion_drops_the_secret_when_done():
    """Spent credentials should not linger on disk."""
    source = (REPO / "cli" / "agentbox").read_text()
    block = source.split("def invite_complete", 1)[1].split("\ndef ", 1)[0]
    assert 'record.pop("secret", None)' in block


def test_vikunja_provisioning_uses_the_container_cli():
    """Registration is disabled on this deployment, so the HTTP API cannot
    create her account — and that privilege is why the web page has none."""
    source = (REPO / "cli" / "agentbox").read_text()
    block = source.split("def provision_vikunja_user", 1)[1].split("\ndef ", 1)[0]
    assert '"docker", "exec"' in block
    assert '"user", "create"' in block


def test_google_is_not_pretended_to_be_automatic():
    """We cannot create a Google account or consent on her behalf, and a flow
    that implied otherwise would leave her sharing Alex's mail."""
    source = (REPO / "cli" / "agentbox").read_text()
    block = source.split("def invite_complete", 1)[1].split("\ndef ", 1)[0]
    assert "cannot be automated" in block
    assert "she shares yours" in block
