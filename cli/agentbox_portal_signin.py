"""Sign-in and account-connection routes, mixed into PortalHandler.

Loaded by cli/agentbox-portal, never imported on its own. Other portal
names are reached through `portal`, the portal module itself.
"""
from __future__ import annotations

import hmac
import html
import json
import secrets
import sys

# The portal that loaded this file, which registers it as "<portal>.<part>".
portal = sys.modules[__name__.rpartition(".")[0]]

__all__ = [
    "SigninRoutes",
]


class SigninRoutes:
    """Sign-in links and account connection routes, mixed into PortalHandler."""

    def _agent_link(self, form) -> None:
        """Mint a sign-in link on the assistant's behalf.

        Authenticated with a token the assistant holds, and deliberately not
        the same one anything else uses. What it produces is an ORIGIN_AGENT
        session, which cannot approve memories or disconnect accounts — so a
        model that decides to call this on its own initiative, or is talked
        into it by a malicious email, gains nothing it did not already have.

        That is the whole design. There is no attempt to stop the assistant
        reading a link it delivered, because it delivers over channels it can
        read and no amount of care changes that.
        """
        provided = self.headers.get("X-Agentbox-Portal-Token", "")
        if not portal.AGENT_TOKEN or not provided or not hmac.compare_digest(
                provided, portal.AGENT_TOKEN):
            self._send(403, portal.page("No", "<h1>No</h1>"))
            return
        identity = (form.get("identity") or [""])[0].strip()
        if not identity:
            self._send(400, portal.page("No", "<h1>identity is required</h1>"))
            return
        try:
            url, _ = portal.mint_link(identity, portal.PUBLIC_URL, origin=portal.ORIGIN_AGENT)
        except RuntimeError as exc:
            self._send(429, portal.page("Slow down", f"<h1>{html.escape(str(exc))}</h1>"))
            return
        body = json.dumps({
            "url": url,
            "expires_in_seconds": portal.LINK_TTL_SECONDS,
            "limits": "This link can review memories and connector status. It "
                      "cannot approve a memory or disconnect an account — for "
                      "those, sign in from the email link.",
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _request_link(self, address: str) -> None:
        """Handle a sign-in request.

        The answer is identical whether or not the address is registered, and
        whether or not delivery succeeded. Anything else turns this form into a
        way to enumerate who lives here.
        """
        identity = portal.identity_for_email(address)
        nonce = secrets.token_urlsafe(24)
        if identity:
            try:
                url, _ = portal.mint_link(identity, portal.PUBLIC_URL, request_nonce=nonce)
                delivered = portal.deliver_link(identity, address, url)
                if not delivered:
                    # Visible to the operator, never to the browser.
                    sys.stderr.write(
                        f"[portal] link for {identity} not delivered by any "
                        f"channel\n")
            except RuntimeError as exc:
                sys.stderr.write(f"[portal] {exc}\n")
        self.send_response(303)
        self.send_header("Location", "/?sent=1")
        self.send_header("Set-Cookie",
                         f"req={nonce}; HttpOnly; SameSite=Lax; Path=/; "
                         f"Max-Age={portal.LINK_TTL_SECONDS}")
        self.end_headers()

    def _google_callback(self, session, query) -> None:
        """Receive Google's authorisation code and spool it for the operator.

        The state is verified against this session rather than merely being
        present. Without that, a link crafted by anyone could land their code
        in someone else's connector record — the account whose mail Agentbox
        then reads would be the attacker's choice, not the user's.
        """
        state = (query.get("state") or [""])[0]
        expected = portal.consent_state(session["identity"])
        if not state or not hmac.compare_digest(state, expected):
            self._send(403, portal.page("Not valid", "<h1>Sorry</h1><p>That consent "
                                 "link did not come from this session. Start "
                                 "again from Your accounts.</p>"))
            return
        code = (query.get("code") or [""])[0]
        if not code:
            # Google's error string is upstream prose and must not be
            # reflected into a page. Logged for the operator, keyed for the user.
            reason = (query.get("error") or ["no code returned"])[0]
            sys.stderr.write(f"[portal] google callback error: {reason[:200]}\n")
            self._redirect("/connectors?m=google_did_not_complete")
            return
        portal.save_request({
            "id": secrets.token_urlsafe(9),
            "identity": session["identity"],
            "connector": "google",
            "action": "reconnect",
            "code": code,
            "redirect_uri": portal.oauth_redirect_uri(),
            "created_at": portal.now(),
            "completed_at": None,
        })
        self._redirect("/connectors?m=consent_received")

    def _connector_action(self, session, form) -> None:
        identity, role = session["identity"], session["role"]
        action = (form.get("action") or [""])[0]
        connector = (form.get("connector") or [""])[0]
        if connector != "google":
            self._redirect("/connectors?m=unknown_connector")
            return
        if action == "reconnect":
            if not portal.can(role, "connector:read_own") or not portal.GOOGLE_CLIENT_ID:
                self._redirect("/connectors?m=google_not_configured")
                return
            # Google will only accept a loopback redirect, so the callback
            # always goes to 127.0.0.1 — which is *this browser's* own machine.
            # Reached from a phone or laptop over the LAN, the consent
            # succeeds and then lands nowhere, and the code is lost. Say so
            # before sending anyone into it rather than after.
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost", "::1", "[::1]"):
                self._send(200, portal.page("Finish this on the box",
                                     portal.wrong_origin_page(host)))
                return
            self._redirect(portal.google_consent_url(portal.consent_state(identity)))
            return
        if action == "disconnect":
            if not portal.can(role, "connector:disconnect_own",
                       session.get("origin", portal.ORIGIN_AGENT)):
                self._redirect("/connectors?m=" + portal.origin_refusal(
                    session.get("origin", portal.ORIGIN_AGENT), "disconnect"))
                return
            portal.save_request({
                "id": secrets.token_urlsafe(9),
                "identity": identity,
                "connector": "google",
                "action": "disconnect",
                "created_at": portal.now(),
                "completed_at": None,
            })
            self._redirect("/connectors?m=disconnect_requested")
            return
        self._redirect("/connectors?m=unknown_action")
