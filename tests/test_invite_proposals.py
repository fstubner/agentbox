"""The assistant may write an invitation. It may not decide who lives here.

`request_signin_link` takes no identity argument, because naming a person is
exactly what an instruction embedded in an email would do. An invitation
cannot be built that way — it is *for* somebody with no session and no
identity — so the binding that keeps the other tool safe is unavailable here.

What replaces it is that the tool does not act. It writes a draft; a human on
Operations decides. These tests are about that substitution holding: the draft
sends nothing, the assistant cannot approve its own draft, and an invitation
naming somebody who already lives here is refused rather than delivered.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys

import pytest
from conftest import REPO_ROOT as REPO
from conftest import code_of, portal_code


def load(name, filename, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOX_ONBOARDING_DIR", str(tmp_path / "onboarding"))
    monkeypatch.setenv("AGENTBOX_INVITE_DIR", str(tmp_path / "invites"))
    monkeypatch.setenv("AGENTBOX_PORTAL_DIR", str(tmp_path / "portal"))
    monkeypatch.setenv("AGENTBOX_ADMINS", "alex")
    monkeypatch.setenv("AGENTBOX_IDENTITY_NAMES", "alex,sam")
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
def portal(tmp_path, monkeypatch, spool):
    return load("agentbox_portal", "agentbox-portal", tmp_path, monkeypatch)


# --- an invitation cannot become a takeover -----------------------------------


def test_inviting_somebody_to_a_name_in_use_is_refused(spool):
    """Completing an invite re-provisions that identity's bridge with whoever
    answered the form. For a name already in use that is not an invitation,
    it is handing somebody the account."""
    with pytest.raises(spool.NameTaken):
        spool.create_invite("alex", {"alex", "sam"})
    # A name nobody holds. Deliberately not one of the household's own names:
    # this assertion is that a *free* name succeeds, so it has to be free.
    made = spool.create_invite("newcomer", {"alex", "sam"})
    assert made["identity"] == "newcomer" and made["secret"]


def test_the_check_cannot_be_forgotten(spool):
    """`existing` is a required argument rather than something looked up, so
    a new caller cannot omit the check by not knowing about it."""
    import inspect
    signature = inspect.signature(spool.create_invite)
    assert signature.parameters["existing"].default is inspect.Parameter.empty


def test_a_name_that_is_not_a_name_is_refused(spool):
    for bad in ("", "9lives", "a" * 33, "has space", "../etc", "-x"):
        with pytest.raises(ValueError):
            spool.create_invite(bad, set())
    # Case is normalised rather than refused: somebody typing a capital is
    # making a typo, not naming a different person.
    assert spool.create_invite("Sam", set())["identity"] == "sam"


# --- a draft is not a send ----------------------------------------------------


def test_a_draft_creates_no_invite_and_sends_nothing(spool, tmp_path):
    spool.propose("alex", "Alex", "alex@example.com", proposed_by="assistant")
    assert len(spool.proposals()) == 1
    # No invite record, so no credential exists yet.
    assert not list((tmp_path / "invites").glob("*.json")) if (
        tmp_path / "invites").exists() else True
    # And nothing queued for the Discord bot to deliver.
    requests = tmp_path / "portal" / "requests"
    assert not requests.exists() or not list(requests.glob("*.json"))


def test_a_draft_does_not_reach_the_onboarding_worker(spool):
    """Proposals live under the watched directory. If they read as pending
    requests, drafting one would trigger privileged completion."""
    spool.propose("alex", "Alex", "alex@example.com")
    assert spool.pending() == []


def test_discarding_leaves_nothing_behind(spool):
    draft = spool.propose("alex", "Alex", "alex@example.com")
    spool.discard_proposal(spool.proposal(draft["id"]))
    assert spool.proposals() == []


# --- the approval step is the whole safety argument ---------------------------


def test_the_assistant_cannot_approve_a_draft(portal):
    """Without this the approval is decoration: the assistant could mint
    itself a link, open its own draft, and send it."""
    for origin in (portal.ORIGIN_AGENT, portal.ORIGIN_CHAT):
        assert not portal.can(portal.ADMIN, "ops:invite", origin), origin


def test_the_handler_checks_the_session_origin(portal):
    body = portal_code().split("def _proposal")[1][:900]
    assert '"ops:invite"' in body
    assert 'session.get("origin"' in body


def test_the_card_will_not_offer_to_send_a_colliding_name(portal, spool):
    """`sam` is a configured identity in this fixture."""
    spool.propose("sam", "Not Sam", "stranger@example.com")
    card = portal.render_proposals_card()
    assert "already lives here" in card
    assert "value=send" not in card
    assert "value=discard" in card


def test_a_draft_with_nowhere_to_go_is_not_sendable(portal, spool):
    spool.propose("newcomer", "Newcomer")
    card = portal.render_proposals_card()
    assert "nowhere to send" in card.lower()
    assert "value=send" not in card


def test_an_ordinary_draft_is_sendable(portal, spool):
    # Not one of the household's own names: proposing a name already in use is
    # refused on purpose, so a sendable draft has to name somebody new.
    spool.propose("newcomer", "Newcomer", "newcomer@example.com")
    card = portal.render_proposals_card()
    assert "value=send" in card and "newcomer@example.com" in card


# --- delivery targets come from the draft, not from the identity map ----------


def test_delivery_does_not_consult_the_identity_lookup(portal):
    """An invitee has no registered address and no paired account — that is
    what an invitation is for. Reusing deliver_link would silently send
    nothing."""
    body = portal_code().split("def deliver_invite")[1][:1400]
    assert "delivery_channels" not in body
    assert "chat_account_for" not in body
    assert 'record.get("address"' in body
    assert 'record.get("discord_user_id"' in body


def test_the_bot_dms_the_id_on_the_record(portal):
    """The approvals bot resolves identities to Discord ids. For an invite
    there is no identity to resolve, so the id rides on the record."""
    body = code_of("cli/agentbox-approvals")
    assert 'action == "deliver_invite"' in body
    assert 'record.get("discord_user_id"' in body
    # And the identity map is still what the sign-in path uses.
    assert "mapping.get(identity)" in body


# --- the tool itself ----------------------------------------------------------


def test_the_tool_cannot_send(portal):
    source = code_of(
        "services/compose/agentbox-mcp/app/integrations/portal.py")
    body = source.split("def _propose_invite")[1]
    assert "/agent/propose-invite" in body
    # It has no route to delivery of any kind.
    for forbidden in ("smtp", "deliver", "/agent/link", "discord("):
        assert forbidden not in body, forbidden


def test_the_tool_says_it_does_not_send(portal):
    """A tool that quietly drafts while the assistant announces it sent an
    invitation is worse than no tool."""
    source = (REPO / "services" / "compose" / "agentbox-mcp" / "app" /
              "integrations" / "portal.py").read_text(encoding="utf-8")
    description = source.split('"name": "propose_invite"')[1][:1200]
    assert "NOT send anything" in description
    assert "admin" in description
    # It must also tell the assistant to say so, or the model relays a
    # confident "invitation sent" over the draft it actually made.
    assert "Say that plainly" in description


def test_the_tool_requires_somewhere_to_send_it(portal, tmp_path,
                                                monkeypatch):
    """An approved invitation with no channel is a dead end an admin only
    discovers after deciding."""
    source = code_of(
        "services/compose/agentbox-mcp/app/integrations/portal.py")
    body = source.split("def _propose_invite")[1][:900]
    assert "not address and not user_id" in body


def test_the_proposed_name_is_not_called_identity(portal):
    """`identity` means "who the assistant is acting as", and never comes from
    an argument — test_portal_tool pins that against the whole module.

    This tool names an account that does not exist yet, which is a different
    thing that happened to want the same word. Renaming it is what let both
    be true; calling it `identity` again would trip that test, and the fix
    would look like relaxing it.
    """
    source = code_of(
        "services/compose/agentbox-mcp/app/integrations/portal.py")
    assert 'args.get("account_name")' in source
    assert 'args.get("identity")' not in source


def test_the_tool_is_tiered_in_the_policy(portal):
    """An unmapped tool fails closed silently, so the gate never sees it."""
    policy = (REPO / "policies" / "approval-policy.yaml").read_text(
        encoding="utf-8")
    assert "propose_invite: propose_invite" in policy


# --- creating an invite from the portal ---------------------------------------
#
# The last step of onboarding that still wanted a terminal. Everything after it
# had already moved: the invitee fills in a web form, an admin finishes it from
# Operations. Starting one meant a keyboard, so a household could not actually
# add a person from a phone.


def test_creating_an_invite_needs_no_privilege(spool, tmp_path):
    """Which is why it had no business needing a shell. One JSON record — no
    docker socket, no bridge token, nothing this page could not already do."""
    record = spool.create_invite("newcomer", {"alex", "sam"})
    written = tmp_path / "invites" / f"{record['id']}.json"
    assert written.exists()
    assert written.stat().st_mode & 0o077 == 0, "the secret is in this file"


def test_the_portal_refuses_to_invite_over_an_existing_person(portal):
    """`create_invite` takes the existing names as a required argument, and
    this is the call site that has to supply them."""
    body = portal_code().split("def _create_invite")[1][:1600]
    assert "known_identities()" in body
    assert "NameTaken" in body


def test_creating_an_invite_is_withheld_from_the_assistant(portal):
    """It produces the credential, where a draft produces only something to
    read — so if either belongs behind a human, it is this one."""
    body = portal_code().split("def _create_invite")[1][:900]
    assert '"ops:invite"' in body
    assert 'session.get("origin"' in body


def test_the_form_is_offered_on_operations(portal):
    card = portal.render_new_invite_card()
    assert "/admin/invite-create" in card
    assert "account_name" in card
    # Both channels, because either is enough to carry it.
    assert "email" in card and "discord_user_id" in card


def test_the_form_says_nothing_is_created_until_approved(portal):
    """Otherwise an admin reasonably assumes pressing this made an account."""
    card = portal.render_new_invite_card().lower()
    assert "approve" in card or "nothing is created" in card


def test_creating_an_invite_always_redirects(portal):
    """Found by an independent acceptance pass.

    The undeliverable path rendered the link straight from the POST — and with
    no delivery channel configured, which is this box, that is every invite. A
    browser refresh re-submitted and minted another live credential; three
    identical submissions produced three valid links for one person.
    """
    body = portal_code().split("def _create_invite")[1][:2200]
    assert "self._redirect" in body
    # The success path may not render a page of its own.
    assert "invite_url" not in body.split("self._redirect")[1][:400]


def test_a_second_live_invite_for_one_person_is_refused(spool):
    """Each one is a credential. Two outstanding for the same name is two."""
    spool.create_invite("newcomer", set())
    with pytest.raises(spool.AlreadyInvited):
        spool.create_invite("newcomer", set())


def test_the_dedupe_cannot_be_forgotten_by_a_caller(spool):
    """Enforced where the record is written, like the name check beside it,
    so the CLI and the portal cannot disagree about it."""
    body = code_of("cli/agentbox_onboarding.py").split("def create_invite")[1][:1200]
    assert "outstanding(" in body
    assert "AlreadyInvited" in body


def test_a_used_invite_stops_blocking_a_new_one(spool):
    """Dedupe is about live credentials, not about the name forever."""
    record = spool.create_invite("newcomer", set())
    path = spool.invite_dir() / f"{record['id']}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["used_at"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")
    assert spool.create_invite("newcomer", set())


def test_the_admin_can_still_reach_a_link_after_a_refresh(portal, spool):
    """What made minting a second one feel reasonable: the link was only ever
    shown once, by the POST that created it."""
    spool.create_invite("newcomer", set())
    card = portal.render_new_invite_card()
    assert "Waiting to be opened" in card
    assert "newcomer" in card
    assert "/?i=" in card


def submitted_invite(tmp_path, token_id, identity, display_name):
    """An invite somebody filled in — what the approval card is built from."""
    path = tmp_path / "invites" / f"{token_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "id": token_id, "identity": identity, "display_name": display_name,
        "secret": "s", "connectors": [], "used_at": 1,
        "expires_at": 9_999_999_999}), encoding="utf-8")


def test_the_approval_card_names_the_account_not_only_the_typed_name(portal, tmp_path):
    """Found by an independent acceptance pass.

    The card showed `display_name or identity or id`, and the form requires a
    display name — so the account name was never shown. An invitee could type
    "sam" and the approving admin would read "sam", while the thing being
    approved was an identity and a bridge for whatever account name the invite
    actually carried.
    """
    submitted_invite(tmp_path, "a" * 16, "stranger", "sam")
    card = portal.render_onboarding_card()
    assert "stranger" in card
    # What they typed is still shown, but as theirs rather than as the subject.
    assert "calls themselves" in card
