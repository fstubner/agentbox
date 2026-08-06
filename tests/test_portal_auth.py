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
    raw = portal.link_path(link_id).read_text()
    assert secret not in raw
    assert "secret_hash" in raw


def test_wrong_secret_is_refused_and_says_nothing(portal):
    _, link_id = portal.mint_link("sam", "http://x")
    identity, _, reason = portal.redeem_link(link_id, "not-the-secret")
    assert identity == ""
    # A refusal that named the wrong part would be an oracle.
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
    is touched — the same rule that binds identity to the session everywhere."""
    monkeypatch.setattr(portal, "memory_call", lambda *a, **k: {
        "proposals": [{"id": "p1", "scope": "alex", "statement": "secret"},
                      {"id": "p2", "scope": "sam", "statement": "hers"}]})
    ok, message = portal.decide_memory("sam", portal.MEMBER, "p1", True)
    assert not ok
    assert "not yours" in message
    assert portal.decide_memory("sam", portal.MEMBER, "p2", True)[0] is True


def test_admin_decides_household_but_not_another_private_scope(portal, monkeypatch):
    """A household memory affects everyone, so somebody must decide it. A
    member's private scope is theirs, and admin is not a master key over it."""
    monkeypatch.setattr(portal, "memory_call", lambda *a, **k: {
        "proposals": [{"id": "h", "scope": "household", "statement": "bins"},
                      {"id": "m", "scope": "sam", "statement": "hers"}]})
    assert portal.decide_memory("alex", portal.ADMIN, "h", True)[0] is True
    ok, message = portal.decide_memory("alex", portal.ADMIN, "m", True)
    assert not ok
    assert "not yours" in message


def test_member_sees_only_their_own_scope(portal, monkeypatch):
    monkeypatch.setattr(portal, "memory_call", lambda *a, **k: {
        "proposals": [{"id": "a", "scope": "alex"},
                      {"id": "b", "scope": "sam"},
                      {"id": "c", "scope": "household"}]})
    assert {p["id"] for p in portal.own_proposals("sam", portal.MEMBER)} == {"b"}
    assert {p["id"] for p in portal.own_proposals("alex", portal.ADMIN)} == \
        {"a", "c"}


def test_bridge_failure_does_not_report_success(portal, monkeypatch):
    """A silent no-op here would tell someone their memory was deleted when it
    was not."""
    monkeypatch.setattr(portal, "own_proposals",
                        lambda *a: [{"id": "p1", "scope": "sam"}])
    monkeypatch.setattr(portal, "memory_call", lambda *a, **k: None)
    ok, message = portal.decide_memory("sam", portal.MEMBER, "p1", True)
    assert not ok
    assert "Nothing changed" in message


# --- containment ---------------------------------------------------------------


def test_portal_is_not_reachable_as_a_tool(portal):
    """The assistant must not be able to mint itself a session.

    The gateway is the Discord bot, so a link delivered over the gateway's own
    connection would be readable by a prompt-injected model, which could then
    approve its own memory proposals and defeat the review gate entirely.
    """
    policy = (REPO / "policies" / "approval-policy.yaml").read_text()
    for forbidden in ("portal_link", "mint_link", "portal_login"):
        assert forbidden not in policy


def test_link_is_bound_to_the_requesting_browser(portal):
    """Reading the link must not be enough to use it.

    The assistant has search_gmail and read_gmail on the very inbox these are
    delivered to. Without this binding, a prompt-injected model could find its
    own login link and approve its own memory proposals — defeating the review
    gate, which is its only route to durable memory.
    """
    url, link_id = portal.mint_link("alex", "http://x", request_nonce="abc123")

    # An interceptor holding the link but not the browser cookie.
    identity, _, reason = portal.redeem_link(link_id, secret_of(url), "")
    assert identity == ""
    assert "browser" in reason
    identity = portal.redeem_link(link_id, secret_of(url), "wrong-nonce")[0]
    assert identity == ""

    # The browser that asked for it.
    assert portal.redeem_link(link_id, secret_of(url), "abc123")[0] == "alex"


def test_failed_binding_does_not_burn_the_link(portal):
    """An attacker must not be able to lock the real user out by touching the
    link first — denial of service is still a failure."""
    url, link_id = portal.mint_link("alex", "http://x", request_nonce="abc")
    portal.redeem_link(link_id, secret_of(url), "wrong")
    assert portal.redeem_link(link_id, secret_of(url), "abc")[0] == "alex"


def test_nonce_is_stored_hashed(portal):
    url, link_id = portal.mint_link("alex", "http://x", request_nonce="abc123")
    assert "abc123" not in portal.link_path(link_id).read_text()


def test_operator_issued_links_need_no_nonce(portal):
    """`portal link` hands the URL over directly; there is no browser to bind."""
    url, link_id = portal.mint_link("sam", "http://x")
    assert portal.redeem_link(link_id, secret_of(url), "")[0] == "sam"


def test_unknown_address_is_indistinguishable(portal):
    """The sign-in form must not reveal who lives here."""
    import inspect
    source = inspect.getsource(portal.PortalHandler._request_link)
    # One response string, built before the lookup and never branched on.
    assert source.count("told = ") == 1
    assert "Location" in source and source.count("told") == 2


def test_email_lookup_is_case_insensitive(portal, monkeypatch):
    monkeypatch.setattr(portal, "IDENTITY_EMAILS", "sam:Sam@Example.com")
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
    """The one capability that actually matters.

    The review gate is the assistant's only route to durable memory. If a link
    it minted could approve, it would be writing its own long-term memory with
    no human in the loop.
    """
    for cap in ("memory:decide_own", "memory:decide_household",
                "connector:disconnect_own"):
        assert not portal.can(portal.ADMIN, cap, portal.ORIGIN_AGENT)
        assert portal.can(portal.ADMIN, cap, portal.ORIGIN_EMAIL)


def test_agent_session_keeps_the_harmless_capabilities(portal):
    """Withholding everything would make the feature pointless. Reading your
    own memories and seeing connector status are things the assistant can
    already do, so a link it made granting them costs nothing."""
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
