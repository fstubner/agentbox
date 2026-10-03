"""Hardening found by running the system rather than by reading the code."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import sys
import time

import pytest
from conftest import REPO_ROOT as REPO


def load(name, filename, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOX_PORTAL_DIR", str(tmp_path / "portal"))
    monkeypatch.setenv("AGENTBOX_ONBOARDING_DIR", str(tmp_path / "onboarding"))
    monkeypatch.setenv("AGENTBOX_INVITE_DIR", str(tmp_path / "invites"))
    monkeypatch.setenv("AGENTBOX_ADMINS", "alex")
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(
            name, str(REPO / "cli" / filename)))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def portal(tmp_path, monkeypatch):
    return load("agentbox_portal", "agentbox-portal", tmp_path, monkeypatch)


@pytest.fixture
def spool(tmp_path, monkeypatch):
    return load("agentbox_onboarding", "agentbox_onboarding.py",
                tmp_path, monkeypatch)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    return load("agentbox_cli", "agentbox", tmp_path, monkeypatch)


class Request:
    """Just enough of a handler for _read_form to run against."""

    def __init__(self, content_length, body=b""):
        self.headers = {"Content-Length": content_length}
        self.rfile = io.BytesIO(body)


# --- F1: a malformed header killed the handler thread -------------------------


def test_a_content_length_that_is_not_a_number_is_refused(portal):
    """A bad length must not kill the handler, which would leave a malformed
    request unanswered while a valid one gets a page, a difference anyone
    outside can measure.
    """
    for bad in ("abc", "1.5", "0x10", "12 34", "+5"):
        assert portal.PortalHandler._read_form(Request(bad)) is None, bad


def test_a_missing_or_empty_header_is_an_empty_form_not_a_failure(portal):
    """A POST with no body is ordinary, not malformed, and an empty header
    says the same thing a missing one does."""
    request = Request(None)
    request.headers = {}
    assert portal.PortalHandler._read_form(request) == {}
    assert portal.PortalHandler._read_form(Request("")) == {}
    # Whitespace says the same thing as empty.
    assert portal.PortalHandler._read_form(Request("   ")) == {}


def test_a_well_formed_body_still_parses(portal):
    body = b"email=someone%40example.com&x=1"
    form = portal.PortalHandler._read_form(Request("31", body))
    assert form["email"] == ["someone@example.com"]


def test_every_malformed_request_answers_the_same_way(portal):
    """Which part was wrong is not the client's business, as on the invite
    page and for link ids."""
    answers = {portal.PortalHandler._read_form(Request(bad))
               for bad in ("abc", "-1", "999999999")}
    assert answers == {None}


# --- F2: the declared size was trusted ----------------------------------------


def test_a_body_larger_than_the_cap_is_refused(portal):
    assert portal.MAX_BODY_BYTES > 0
    over = str(portal.MAX_BODY_BYTES + 1)
    assert portal.PortalHandler._read_form(Request(over)) is None


def test_the_cap_is_generous_enough_for_a_real_form(portal):
    """Every form here is a handful of short fields."""
    assert portal.MAX_BODY_BYTES >= 8192


def test_a_negative_length_is_refused(portal):
    assert portal.PortalHandler._read_form(Request("-1")) is None


def test_only_ascii_digits_count_as_a_length(portal):
    """int() accepts other numeral systems; a header the spec defines as
    DIGIT is not the place to be inventive."""
    assert portal.PortalHandler._read_form(Request("\u0663")) is None


def test_undecodable_bytes_do_not_escape(portal):
    """parse_qs on invalid UTF-8 raises, in the same place and with the same
    consequence as the header did."""
    assert portal.PortalHandler._read_form(Request("2", b"\xff\xfe")) is None


# --- F3: the approval step degrades under volume ------------------------------


def test_the_waiting_queue_is_bounded(portal, spool):
    """Drafts buy no privilege. What a flood buys is an admin who stops
    reading, and the reading is the entire safeguard for this tool."""
    assert portal.MAX_PENDING_PROPOSALS >= 1
    for i in range(portal.MAX_PENDING_PROPOSALS):
        spool.propose(f"person{i}", f"Person {i}", f"p{i}@example.com")
    assert len(spool.proposals()) == portal.MAX_PENDING_PROPOSALS


def test_the_cap_clears_itself_as_the_admin_acts(portal, spool):
    """A depth bound rather than a rate limit, so answering one makes room."""
    drafts = [spool.propose(f"p{i}", f"P{i}", f"p{i}@example.com")
              for i in range(portal.MAX_PENDING_PROPOSALS)]
    spool.discard_proposal(spool.proposal(drafts[0]["id"]))
    assert len(spool.proposals()) == portal.MAX_PENDING_PROPOSALS - 1


def test_the_tool_explains_the_refusal(portal):
    """A 429 relayed as a number makes the model retry into a wall."""
    source = (REPO / "services" / "compose" / "agentbox-mcp" / "app" /
              "integrations" / "portal.py").read_text(encoding="utf-8")
    body = source.split("def _propose_invite")[1]
    assert "429" in body
    assert "waiting" in body.lower()


# --- F6: the spool grew forever -----------------------------------------------


def write_invite(tmp_path, token_id, **over):
    record = {"id": token_id, "identity": "someone", "secret": "s",
              "expires_at": int(time.time()) + 3600, "used_at": None}
    record.update(over)
    path = tmp_path / "invites" / f"{token_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_an_invite_that_expired_unused_is_dropped(spool, tmp_path):
    """Its secret is still in the file, and that secret is what made the link
    a credential. Nobody can redeem it; keeping it buys nothing."""
    path = write_invite(tmp_path, "a" * 16, expires_at=int(time.time()) - 1)
    assert spool.reap()[1] == 1
    assert not path.exists()


def test_an_invite_waiting_on_an_admin_is_kept_however_old(spool, tmp_path):
    """Submitted and not yet completed. Somebody is waiting on this."""
    path = write_invite(tmp_path, "b" * 16,
                        expires_at=int(time.time()) - 99999, used_at=1)
    spool.reap()
    assert path.exists()


def test_a_completed_invite_is_kept_permanently(spool, tmp_path):
    """Its secret was dropped at completion, and what remains is the record of
    somebody joining the household."""
    path = write_invite(tmp_path, "c" * 16,
                        expires_at=1, used_at=1, completed_at=2)
    spool.reap()
    assert path.exists()


def test_settled_requests_age_out_but_not_quickly(spool):
    """History, not a credential, so it is kept for a window rather than
    deleted. It answers "who let this person in"."""
    spool.request("d" * 16, "alex")
    [record] = spool.pending()
    spool.settle(record, "completed")
    kept = spool.settled_dir() / f"{'d' * 16}.json"
    assert spool.reap()[0] == 0 and kept.exists()
    old = int(time.time()) + spool.SETTLED_RETENTION + 60
    assert spool.reap(now=old)[0] == 1
    assert not kept.exists()


def test_reaping_survives_an_unreadable_file(spool, tmp_path):
    """Runs at startup beside the portal's own reaper. One bad file is worth
    skipping, not worth refusing to serve over."""
    (tmp_path / "invites").mkdir(parents=True, exist_ok=True)
    (tmp_path / "invites" / "broken.json").write_text("{nope", encoding="utf-8")
    assert spool.reap() == (0, 0)


# --- F5: the gate could not see its own worst case ----------------------------


def test_the_size_gate_sees_extensionless_files():
    """Generic size checkers only look at *.py, so the extensionless CLI
    scripts must be measured too."""
    import agentbox_validate as cli
    names = {str(p.relative_to(cli.REPO)) for p in cli.source_files()}
    assert "cli/agentbox" in names
    assert "cli/agentbox-portal" in names
    assert "cli/agentbox_status.py" in names


def test_the_entry_points_stay_small():
    """Both commands were split into modules once they passed 2,400 lines.
    Keeping them off the exceptions list means growing either one again fails
    validate rather than quietly raising a ceiling."""
    import agentbox_validate as cli
    for name in ("cli/agentbox", "cli/agentbox-portal"):
        assert name not in cli.LARGE_FILES, name


def test_the_repo_is_within_its_own_ceilings():
    """Each recorded number is a ceiling. Files may shrink and must not grow."""
    import agentbox_validate as cli
    oversized, grown = cli.file_size_drift()
    assert not oversized, oversized
    assert not grown, grown


# --- found by an independent acceptance pass --------------------------------


def test_a_finished_invitee_is_not_told_to_ask_for_a_new_link(tmp_path,
                                                              monkeypatch):
    """They already did the only thing asked of them.

    The confirmation page is the POST response, so a refresh or a second tap on
    the link lands here. It must not tell them to ask for a new link, which
    would create a second credential for no reason.
    """
    invite = load("agentbox_invite", "agentbox-invite", tmp_path, monkeypatch)
    token_id = "e" * 16
    path = tmp_path / "invites" / f"{token_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "id": token_id, "identity": "someone", "secret": "s",
        "used_at": 1, "expires_at": int(time.time()) + 3600}),
        encoding="utf-8")
    _, reason = invite.valid_invite(token_id, "s")
    assert "ask for a new one" not in reason.lower()
    assert "already filled this in" in reason.lower()
    assert "sign in" in reason.lower()


def test_operations_does_not_claim_the_portal_is_unauthenticated():
    """It authenticates every route, including unknown ones, so the health view
    must not warn otherwise. A false warning gets ignored.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "agentbox_status", REPO / "cli" / "agentbox_status.py")
    status = importlib.util.module_from_spec(spec)
    sys.modules["agentbox_status"] = status
    spec.loader.exec_module(status)
    for check in status.lan_exposure():
        if ":8771" in check.detail:
            assert "asks for no password" not in check.detail
            assert "authenticates every route" in check.detail
            break


def test_the_exposure_warning_does_not_assert_what_it_cannot_know():
    """The original wording was applied to every watched port. It is knowable
    for the portal and not for most of the others, and asserting it anyway is
    what made it wrong."""
    source = (REPO / "cli" / "agentbox_status_checks.py").read_text(encoding="utf-8")
    body = source.split("def lan_exposure")[1]
    assert "cannot confirm it requires a credential" in body
