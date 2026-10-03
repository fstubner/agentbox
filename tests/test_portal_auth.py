"""The portal's authorisation model.

These test the properties that would matter if the model were compromised:
that a link cannot be replayed, that a role cannot be claimed, and that a form
field cannot decide whose memory is being touched.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest
from conftest import code_of, portal_code

REPO = Path(__file__).resolve().parents[1]


def secret_of(url: str) -> str:
    """The one-time secret exists only in the minted URL, never at rest."""
    import urllib.parse
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["k"][0]


def load_portal(tmp_path, monkeypatch, admins: str = "alex"):
    monkeypatch.setenv("AGENTBOX_PORTAL_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTBOX_ADMINS", admins)
    spec = importlib.util.spec_from_loader(
        "agentbox_portal",
        importlib.machinery.SourceFileLoader(
            "agentbox_portal", str(REPO / "cli" / "agentbox-portal")))
    module = importlib.util.module_from_spec(spec)
    sys.modules["agentbox_portal"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def portal(tmp_path, monkeypatch):
    return load_portal(tmp_path, monkeypatch)


# --- roles ---------------------------------------------------------------------


def test_roles_deny_by_default(portal):
    """An unknown role gets nothing, rather than falling through to member."""
    assert portal.can(portal.MEMBER, "memory:decide_own")
    assert not portal.can(portal.MEMBER, "ops:read_health")
    assert not portal.can("", "memory:read_own")
    assert not portal.can("superuser", "ops:read_health")


def test_empty_admin_list_grants_nobody(tmp_path, monkeypatch):
    """A misread env file must remove privilege, never hand it out."""
    module = load_portal(tmp_path, monkeypatch, admins="")
    assert module.admin_names() == set()
    assert module.role_of("alex") == module.MEMBER
    assert not module.can(module.role_of("alex"), "ops:read_health")


def test_member_cannot_reach_operations(portal):
    assert portal.role_of("sam") == portal.MEMBER
    assert not portal.can(portal.role_of("sam"), "ops:read_health")
    assert not portal.can(portal.role_of("sam"),
                          "ops:stage_messaging_credential")


# --- magic links ---------------------------------------------------------------


def test_link_is_single_use(portal):
    url, link_id = portal.mint_link("sam", "http://x")
    secret = secret_of(url)
    assert portal.redeem_link(link_id, secret)[0] == "sam"
    identity, _, reason = portal.redeem_link(link_id, secret)
    assert identity == ""
    assert "already been used" in reason


def test_link_expires(portal, monkeypatch):
    url, link_id = portal.mint_link("sam", "http://x")
    secret = secret_of(url)
    expired = portal.now() + portal.LINK_TTL_SECONDS + 60
    monkeypatch.setattr(portal, "now", lambda: expired)
    identity, _, reason = portal.redeem_link(link_id, secret)
    assert identity == ""
    assert "expired" in reason


def test_link_secret_is_not_stored(portal):
    """Reading the state directory must not yield a usable link."""
    url, link_id = portal.mint_link("sam", "http://x")
    secret = secret_of(url)
    raw = code_of(portal.link_path(link_id))
    assert secret not in raw
    assert "secret_hash" in raw


def test_wrong_secret_is_refused_and_says_nothing(portal):
    _, link_id = portal.mint_link("sam", "http://x")
    identity, _, reason = portal.redeem_link(link_id, "not-the-secret")
    assert identity == ""
    # A refusal that named the wrong part would reveal which part was right.
    assert reason == portal.redeem_link("no-such-link", "x")[2]


def test_link_minting_is_rate_limited(portal):
    for _ in range(portal.MAX_LINKS_PER_HOUR):
        portal.mint_link("sam", "http://x")
    with pytest.raises(RuntimeError):
        portal.mint_link("sam", "http://x")


def test_redemption_is_constant_time(portal):
    """A byte-by-byte comparison leaks the secret one character at a time."""
    import inspect
    assert "compare_digest" in inspect.getsource(portal.redeem_link)


# --- sessions ------------------------------------------------------------------


def test_session_role_is_recomputed_not_trusted(portal, monkeypatch):
    """Removing an admin must take effect now, not at session expiry."""
    sid = portal.new_session("alex")
    assert portal.load_session(sid)["role"] == portal.ADMIN
    monkeypatch.setenv("AGENTBOX_ADMINS", "")
    assert portal.load_session(sid)["role"] == portal.MEMBER


def test_session_cookie_is_not_stored_verbatim(portal):
    sid = portal.new_session("sam")
    stored = list((portal.STATE / "sessions").glob("*.json"))
    assert stored and sid not in stored[0].name
    assert sid not in stored[0].read_text()


def test_expired_session_resolves_to_nobody(portal, monkeypatch):
    sid = portal.new_session("sam")
    expired = portal.now() + portal.SESSION_TTL_SECONDS + 60
    monkeypatch.setattr(portal, "now", lambda: expired)
    assert portal.load_session(sid) is None


def test_garbage_cookie_resolves_to_nobody(portal):
    assert portal.load_session("") is None
    assert portal.load_session("../../etc/passwd") is None


# --- memory scope enforcement --------------------------------------------------


def test_member_cannot_decide_another_identity_proposal(portal, monkeypatch):
    """The id is a form field, and a form field must not choose whose memory
    is touched."""
    monkeypatch.setattr(portal, "memory_call", lambda *a, **k: {
        "proposals": [{"id": "p1", "scope": "alex", "statement": "secret"},
                      {"id": "p2", "scope": "sam", "statement": "hers"}]})
    ok, message = portal.decide_memory("sam", portal.MEMBER, "p1", True)
    assert not ok
    assert message == "not_yours"
    assert "not yours" in portal.flash_text(message)
    assert portal.decide_memory("sam", portal.MEMBER, "p2", True)[0] is True


def test_admin_decides_household_but_not_another_private_scope(portal, monkeypatch):
    """A household memory affects everyone, so somebody must decide it. The
    admin role cannot override a member's private scope."""
    monkeypatch.setattr(portal, "memory_call", lambda *a, **k: {
        "proposals": [{"id": "h", "scope": "household", "statement": "bins"},
                      {"id": "m", "scope": "sam", "statement": "hers"}]})
    assert portal.decide_memory("alex", portal.ADMIN, "h", True)[0] is True
    ok, message = portal.decide_memory("alex", portal.ADMIN, "m", True)
    assert not ok
    assert message == "not_yours"
    assert "not yours" in portal.flash_text(message)


def test_member_sees_only_their_own_scope(portal, monkeypatch):
    monkeypatch.setattr(portal, "memory_call", lambda *a, **k: {
        "proposals": [{"id": "a", "scope": "alex"},
                      {"id": "b", "scope": "sam"},
                      {"id": "c", "scope": "household"}]})
    assert {p["id"] for p in portal.own_proposals("sam", portal.MEMBER)} == {"b"}
    assert {p["id"] for p in portal.own_proposals("alex", portal.ADMIN)} == \
        {"a", "c"}


def test_bridge_failure_does_not_report_success(portal, monkeypatch):
    """Reporting success here would tell someone their memory was deleted when
    it was not."""
    monkeypatch.setattr(portal, "own_proposals",
                        lambda *a: [{"id": "p1", "scope": "sam"}])
    monkeypatch.setattr(portal, "memory_call", lambda *a, **k: None)
    ok, message = portal.decide_memory("sam", portal.MEMBER, "p1", True)
    assert not ok
    assert message == "memory_service_down"
    assert "Nothing changed" in portal.flash_text(message)


# --- containment ---------------------------------------------------------------


def test_portal_is_not_reachable_as_a_tool(portal):
    """The assistant must not be able to mint itself a session.

    The gateway is the Discord bot, so a link delivered over the gateway's own
    connection would be readable by a prompt-injected model, which could then
    approve its own memory proposals and bypass the review gate.
    """
    policy = code_of(REPO / "policies" / "approval-policy.yaml")
    for forbidden in ("portal_link", "mint_link", "portal_login"):
        assert forbidden not in policy


def test_link_is_bound_to_the_requesting_browser(portal):
    """Reading the link must not be enough to use it.

    The assistant can search and read the inbox these are sent to. Without the
    binding, an injected model could find its own sign-in link and approve its
    own memory proposals, which would defeat the review gate.
    """
    url, link_id = portal.mint_link("alex", "http://x", request_nonce="abc123")

    # Someone with the link but not the browser cookie gets a session, as
    # opening a link on a phone does, but cannot approve or disconnect.
    identity, origin, reason = portal.redeem_link(link_id, secret_of(url), "")
    assert identity == "alex" and reason == ""
    assert origin == portal.ORIGIN_CHAT
    assert not portal.can(portal.ADMIN, "memory:decide_own", origin)
    assert not portal.can(portal.ADMIN, "connector:disconnect_own", origin)
    # A guessed nonce is no better than none.
    assert portal.redeem_link(link_id, secret_of(url), "wrong-nonce")[1] == \
        portal.ORIGIN_CHAT

    # The browser that asked for it gets everything.
    identity, origin, _ = portal.redeem_link(link_id, secret_of(url), "abc123")
    assert identity == "alex"
    assert portal.can(portal.ADMIN, "memory:decide_own", origin)


def test_a_downgraded_redemption_does_not_burn_the_link(portal):
    """Opening the link first must not lock the real person out, and a new
    link would arrive on the same channel the reader is watching."""
    url, link_id = portal.mint_link("alex", "http://x", request_nonce="abc")
    # A reader who opens it gets a limited session, and the link stays valid.
    assert portal.redeem_link(link_id, secret_of(url), "wrong")[1] == \
        portal.ORIGIN_CHAT
    identity, origin, _ = portal.redeem_link(link_id, secret_of(url), "abc")
    assert identity == "alex"
    assert portal.can(portal.ADMIN, "memory:decide_own", origin)


def test_full_access_is_still_single_use(portal):
    """Only the privileged redemption spends the link."""
    url, link_id = portal.mint_link("alex", "http://x", request_nonce="abc")
    assert portal.redeem_link(link_id, secret_of(url), "abc")[0] == "alex"
    assert portal.redeem_link(link_id, secret_of(url), "abc")[0] == ""


def test_nonce_is_stored_hashed(portal):
    url, link_id = portal.mint_link("alex", "http://x", request_nonce="abc123")
    assert "abc123" not in portal.link_path(link_id).read_text()


def test_operator_issued_links_need_no_nonce(portal):
    """`portal link` hands the URL over directly, with no browser to bind."""
    url, link_id = portal.mint_link("sam", "http://x")
    assert portal.redeem_link(link_id, secret_of(url), "")[0] == "sam"


class _CapturingHandler:
    """Enough of BaseHTTPRequestHandler to see exactly what was sent back."""

    def __init__(self, portal):
        self.sent = []
        self._request_link = portal.PortalHandler._request_link.__get__(self)

    def send_response(self, code):
        self.sent.append(("status", code))

    def send_header(self, key, value):
        self.sent.append((key, value))

    def end_headers(self):
        pass


def test_unknown_address_is_indistinguishable(portal, monkeypatch):
    """The sign-in form must not reveal who lives here.

    This runs both requests instead of reading the source. The response to a
    registered address and an unregistered one must be byte-identical apart
    from the nonce, and only running both can show that.
    """
    portal.SETTINGS.save({"identity_emails": "alex:alex@example.com"})
    monkeypatch.setattr(portal, "send_link_email", lambda a, u: (True, ""))

    def response_for(address):
        handler = _CapturingHandler(portal)
        handler._request_link(address)
        # The nonce differs by design; everything else must not.
        return [(k, v.split(";")[0] if k == "Set-Cookie" else v)
                for k, v in handler.sent if k != "Set-Cookie"]

    assert response_for("alex@example.com") == response_for("nobody@example.com")
    assert ("Location", "/?sent=1") in response_for("nobody@example.com")


def test_email_lookup_is_case_insensitive(portal, monkeypatch):
    portal.SETTINGS.save({"identity_emails": "sam:Sam@Example.com"})
    assert portal.identity_for_email("sam@example.COM") == "sam"
    assert portal.identity_for_email("someone@else.com") == ""


def test_portal_sets_defensive_response_headers(portal):
    import inspect
    source = inspect.getsource(portal.PortalHandler._send)
    for header in ("X-Frame-Options", "X-Content-Type-Options",
                   "Referrer-Policy", "no-store"):
        assert header in source


def test_session_cookie_is_httponly_and_samesite(portal):
    import inspect
    source = inspect.getsource(portal.PortalHandler.do_GET)
    assert "HttpOnly" in source and "SameSite=Lax" in source




# --- agent-minted links --------------------------------------------------------


def test_agent_session_cannot_approve_memories(portal):
    """This is the capability that must be withheld.

    The review gate is the assistant's only route to durable memory. If a link
    it minted could approve, it would be writing its own long-term memory with
    no human in the loop.
    """
    for cap in ("memory:decide_own", "memory:decide_household",
                "connector:disconnect_own"):
        assert not portal.can(portal.ADMIN, cap, portal.ORIGIN_AGENT)
        assert portal.can(portal.ADMIN, cap, portal.ORIGIN_EMAIL)


def test_agent_session_keeps_the_harmless_capabilities(portal):
    """Withholding everything would make the link useless. The assistant can
    already read your memories and see connector status, so a link it made
    that grants them adds no risk."""
    for cap in ("memory:read_own", "connector:read_own"):
        assert portal.can(portal.MEMBER, cap, portal.ORIGIN_AGENT)


def test_unknown_origin_is_treated_as_agent(portal):
    """A value read from disk that is missing or unrecognised must fail toward
    less privilege, not more."""
    assert not portal.can(portal.ADMIN, "memory:decide_own", "")
    assert not portal.can(portal.ADMIN, "memory:decide_own", "something-else")


def test_origin_survives_the_round_trip(portal):
    url, link_id = portal.mint_link("alex", "http://x",
                                    origin=portal.ORIGIN_AGENT)
    identity, origin, reason = portal.redeem_link(link_id, secret_of(url))
    assert (identity, origin, reason) == ("alex", portal.ORIGIN_AGENT, "")
    sid = portal.new_session(identity, origin)
    assert portal.load_session(sid)["origin"] == portal.ORIGIN_AGENT


def test_operator_and_email_links_keep_full_rights(portal):
    url, link_id = portal.mint_link("alex", "http://x")
    _, origin, _ = portal.redeem_link(link_id, secret_of(url))
    assert origin == portal.ORIGIN_OPERATOR
    assert portal.can(portal.ADMIN, "memory:decide_own", origin)


def test_decide_memory_refuses_an_agent_session(portal, monkeypatch):
    monkeypatch.setattr(portal, "memory_call", lambda *a, **k: {
        "proposals": [{"id": "p1", "scope": "alex"}]})
    with pytest.raises(PermissionError):
        portal.decide_memory("alex", portal.ADMIN, "p1", True,
                             portal.ORIGIN_AGENT)
    ok, _ = portal.decide_memory("alex", portal.ADMIN, "p1", True,
                                 portal.ORIGIN_EMAIL)
    assert ok is True


def test_agent_endpoint_is_absent_when_unconfigured(portal):
    """A capability nobody configured should not exist."""
    assert portal.AGENT_TOKEN == ""


def test_startup_warns_when_links_cannot_be_delivered(portal):
    """Startup is the only place this failure can be shown.

    The sign-in page answers the same for known and unknown addresses, so it
    cannot report that delivery failed. Someone is told a link is coming and
    nothing arrives.
    """
    import inspect
    source = inspect.getsource(portal.cmd_serve)
    # Addresses configured with no way to deliver to them.
    assert 'SETTINGS.value("identity_emails")' in source
    assert 'not config.SETTINGS.value("smtp_host")' in source
    assert "never sent" in source
    # It names the two alternatives, since the page cannot.
    assert "Discord" in source


def test_delivery_failure_is_never_revealed_to_the_browser(portal, monkeypatch):
    """The other half of the same design. The operator is told and the
    visitor is not."""
    import inspect
    portal.SETTINGS.save({"identity_emails": "alex:alex@example.com"})
    assert "sys.stderr.write" in inspect.getsource(
        portal.PortalHandler._request_link)

    def response_when(sent):
        monkeypatch.setattr(portal, "send_link_email",
                            lambda a, u: (sent, "" if sent else "SMTPError"))
        handler = _CapturingHandler(portal)
        handler._request_link("alex@example.com")
        return [(k, v) for k, v in handler.sent if k != "Set-Cookie"]

    assert response_when(True) == response_when(False)


# --- editing and classifying at review time ------------------------------------


def test_the_edit_box_carries_the_current_wording(portal):
    """The reviewer edits what the assistant proposed, not a blank box."""
    portal.own_proposals = lambda identity, role: [
        {"id": "p1", "scope": "alex", "statement": "Alex hates meetings",
         "kind": "memory"}]
    body = portal.render_home("alex", portal.ADMIN, "").decode()
    assert "<textarea name=statement" in body
    assert "Alex hates meetings" in body


def test_a_feedback_proposal_says_why_and_offers_the_split(portal):
    portal.own_proposals = lambda identity, role: [
        {"id": "p2", "scope": "alex", "statement": "You keep asking me twice",
         "kind": "feedback", "kind_reason": "sounds like feedback (“you keep”)"}]
    body = portal.render_home("alex", portal.ADMIN, "").decode()
    assert "feedback about how I behave" in body
    assert "value=feedback" in body


def test_filing_as_feedback_sends_the_kind_to_the_bridge(portal, monkeypatch):
    """Both verbs use one path. The bridge routes anything marked feedback to
    the backlog, so the two cannot disagree about where it lands."""
    sent = {}
    portal.own_proposals = lambda identity, role: [
        {"id": "p3", "scope": "alex", "statement": "You keep asking"}]
    monkeypatch.setattr(portal, "memory_call",
                        lambda method, path, payload=None: sent.update(
                            path=path, payload=payload) or {"status": "open"})
    ok, message = portal.decide_memory("alex", portal.ADMIN, "p3", "feedback",
                                       statement="You keep asking")
    assert ok
    assert sent["path"].endswith("/approve")
    assert sent["payload"]["kind"] == "feedback"
    assert message == "filed_as_feedback"
    assert "not saved as a memory" in portal.flash_text(message)


def test_an_edited_statement_reaches_the_bridge(portal, monkeypatch):
    sent = {}
    portal.own_proposals = lambda identity, role: [
        {"id": "p4", "scope": "alex", "statement": "Alex hates meetings"}]
    monkeypatch.setattr(portal, "memory_call",
                        lambda method, path, payload=None: sent.update(
                            payload=payload) or {"status": "approved"})
    ok, message = portal.decide_memory(
        "alex", portal.ADMIN, "p4", "approve",
        statement="Alex dislikes meetings before 10am")
    assert ok
    assert sent["payload"]["statement"] == "Alex dislikes meetings before 10am"
    assert message == "memory_saved_edited"
    assert "with your edit" in portal.flash_text(message)


def test_an_agent_minted_session_still_cannot_decide(portal):
    """Editing must not have opened a route around the origin gate."""
    portal.own_proposals = lambda identity, role: [
        {"id": "p5", "scope": "alex", "statement": "x"}]
    with pytest.raises(PermissionError):
        portal.decide_memory("alex", portal.ADMIN, "p5", "approve",
                             origin=portal.ORIGIN_AGENT, statement="edited")


# --- stored memories, history, and forgetting ---------------------------------


def _memories_payload(rows):
    return {"memories": rows, "total": len(rows)}


def test_the_page_shows_what_is_actually_stored(portal, monkeypatch):
    """Without this the portal could only add memories, never show or correct
    them."""
    portal.own_proposals = lambda identity, role: []
    monkeypatch.setattr(portal, "memory_call", lambda m, p, payload=None:
                        _memories_payload([
                            {"id": "m1", "scope": "household", "status": "approved",
                             "statement": "Bin day is Wednesday"}]))
    body = portal.render_home("alex", portal.ADMIN, "").decode()
    assert "What I remember" in body
    assert "Bin day is Wednesday" in body
    assert "Forget this" in body


def test_earlier_versions_are_shown_as_history(portal, monkeypatch):
    """The useful record is that this replaced something, and what."""
    portal.own_proposals = lambda identity, role: []
    monkeypatch.setattr(portal, "memory_call", lambda m, p, payload=None:
                        _memories_payload([
                            {"id": "old", "scope": "household",
                             "status": "superseded",
                             "statement": "Bin day is Tuesday"},
                            {"id": "new", "scope": "household",
                             "status": "approved", "supersedes": "old",
                             "statement": "Bin day is Wednesday"}]))
    body = portal.render_home("alex", portal.ADMIN, "").decode()
    assert "1 earlier version" in body
    assert "was: Bin day is Tuesday" in body
    # The superseded one is history, not a second current memory.
    assert body.count("Forget this") == 1


def test_another_persons_memory_is_not_listed(portal, monkeypatch):
    portal.own_proposals = lambda identity, role: []
    monkeypatch.setattr(portal, "memory_call", lambda m, p, payload=None:
                        _memories_payload([
                            {"id": "m1", "scope": "sam", "status": "approved",
                             "statement": "Sam's private thing"}]))
    body = portal.render_home("alex", portal.MEMBER, "").decode()
    assert "Sam's private thing" not in body


def test_forgetting_out_of_scope_is_refused(portal, monkeypatch):
    """An id posted from a crafted form must never decide whose memory is
    touched."""
    monkeypatch.setattr(portal, "memory_call", lambda m, p, payload=None:
                        _memories_payload([
                            {"id": "mine", "scope": "alex", "status": "approved",
                             "statement": "x"}]))
    ok, message = portal.forget_memory("alex", portal.MEMBER, "someone-elses")
    assert not ok
    assert message == "not_yours"
    assert "not yours" in portal.flash_text(message)


def test_an_agent_session_cannot_forget(portal, monkeypatch):
    """Deleting memories is the same capability as writing them, in
    reverse."""
    monkeypatch.setattr(portal, "memory_call", lambda m, p, payload=None:
                        _memories_payload([]))
    with pytest.raises(PermissionError):
        portal.forget_memory("alex", portal.ADMIN, "m1",
                             origin=portal.ORIGIN_AGENT)


def test_the_signed_out_page_never_echoes_the_url(portal):
    """A message meant for a signed-in page must not be shown to someone
    opening an old URL, and must not tell a visitor what the box was doing."""
    body = portal.render_signin(sent=False).decode()
    assert "Google consent" not in body
    assert "class=flash" not in body
    # The one message that belongs there is fixed text, not from the URL.
    sent = portal.render_signin(sent=True).decode()
    assert "a sign-in link is on its way" in sent


def test_requesting_a_link_redirects_without_a_message_parameter(portal):
    source = portal_code()
    block = source.split("def _request_link")[1].split("def ")[0]
    assert '"/?sent=1"' in block
    assert "urlencode({\"m\"" not in block


# --- flash messages ------------------------------------------------------------


def test_no_page_renders_free_text_from_the_url(portal):
    """Messages come from keys, never from free text in the URL, so they
    cannot be cut off or linger in a bookmarked link."""
    from conftest import portal_code
    source = portal_code()
    assert 'urlencode({"m"' not in source
    # The only thing read from the URL is a short key, looked up in a table.
    assert "flash_text(" in source


def test_an_unknown_key_renders_nothing_rather_than_itself(portal):
    """Otherwise `?m=<script>` is reflected content, keyed or not."""
    assert portal.flash_text("no_such_key") == ""
    assert portal.flash_text("<b>hi</b>") == ""


def test_every_key_the_portal_redirects_to_actually_exists(portal):
    """A mistyped key would show a blank message and look like nothing
    happened."""
    import re

    from conftest import portal_code
    source = portal_code()
    used = set(re.findall(r'[?&]m=([a-z_]+)"', source))
    used |= set(re.findall(r'message = "([a-z_]+)"', source))
    unknown = sorted(k for k in used if k not in portal.FLASHES)
    assert not unknown, f"redirects to keys with no message: {unknown}"


def test_messages_are_not_truncated(portal):
    """The text lives in code, so it is never cut off."""
    assert portal.flash_text("consent_received").endswith("if you can.")
    for key, text in portal.FLASHES.items():
        assert text.strip(), key
        assert not text.endswith(("…", " if y")), key
