"""The portal's HTTP request handler.

Loaded by cli/agentbox-portal, never imported on its own. Other portal
names are reached through `portal`, the portal module itself.
"""
from __future__ import annotations

import html
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

# The portal that loaded this file, which registers it as "<portal>.<part>".
portal = sys.modules[__name__.rpartition(".")[0]]

__all__ = [
    "wrong_origin_page",
    "PortalHandler",
]


def wrong_origin_page(host: str) -> str:
    """Explain why connecting Google has to happen on the box itself.

    Without this the failure is confusing. Consent succeeds, Google redirects to
    127.0.0.1, and the browser lands on whatever is running on the person's own
    machine, usually nothing.
    """
    port = urllib.parse.urlparse(portal.PUBLIC_URL).port or 8771
    return (
        "<h1>Finish this on the box</h1>"
        "<p class=sub>Connecting a Google account is the one thing that "
        f"cannot be done from another device. You are on <b>{html.escape(host)}"
        f"</b>.</p>"
        "<div class=card><b>Why</b><p class=sub style='margin:.4rem 0 0'>"
        "Google only accepts a loopback address as the return URL, so after "
        f"you approve, it sends the code to <code>127.0.0.1:{port}</code>, "
        "which is <i>this</i> device, not Agentbox. The approval works and "
        "the code lands nowhere.</p></div>"
        "<div class=card><b>Option 1: use a browser on the Agentbox machine"
        "</b><p class=sub style='margin:.4rem 0 0'>Open "
        f"<code>http://127.0.0.1:{port}</code> there, sign in, and press "
        "Reconnect.</p></div>"
        "<div class=card><b>Option 2: tunnel from here</b>"
        "<p class=sub style='margin:.4rem 0 .4rem'>Run this on the device you "
        "are using now, then open the portal at "
        f"<code>http://127.0.0.1:{port}</code> in this browser:</p>"
        f"<pre style='background:#f6f7f9;border:1px solid #e3e6ea;"
        f"border-radius:.4rem;padding:.6rem;overflow-x:auto'>ssh -L "
        f"{port}:127.0.0.1:{port} {html.escape(os.environ.get('USER', 'alex'))}"
        f"@{html.escape(host)}</pre></div>"
        "<p><a href='/connectors'>Back to accounts</a></p>")

class PortalHandler(portal.PortalActions, portal.SigninRoutes, BaseHTTPRequestHandler):
    server_version = "agentbox-portal"

    def log_message(self, fmt, *args):  # noqa: A002
        sys.stderr.write(f"[portal] {self._redact(fmt % args)}\n")

    @staticmethod
    def _redact(line: str) -> str:
        """Strip query strings before anything reaches the log.

        The standard library logs the whole request line, which includes a
        sign-in link's secret in `/login?id=…&k=<secret>`. The secret is only
        stored hashed, so writing it to the journal would undo that. Every
        query string is redacted, so the next sensitive parameter is covered
        without anyone remembering to add it.
        """
        return re.sub(r"(\?)\S*", r"\1<redacted>", line)

    def _send(self, code: int, body: bytes, headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # This page shows private memories and can revoke credentials, so it
        # must not be framed, MIME-sniffed or leaked through a referrer.
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _session(self) -> dict | None:
        return portal.load_session(portal.cookie_value(self.headers.get("Cookie", ""), "sid"))

    def _redirect(self, location: str, headers: dict | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        flash = portal.flash_text((query.get("m") or [""])[0][:40])

        if parsed.path == "/login":
            identity, origin, reason = portal.redeem_link(
                (query.get("id") or [""])[0], (query.get("k") or [""])[0],
                portal.cookie_value(self.headers.get("Cookie", ""), "req"))
            if not identity:
                self._send(403, portal.page("Not valid", f"<h1>Sorry</h1>"
                                     f"<p>{html.escape(reason)}</p>"))
                return
            sid = portal.new_session(identity, origin)
            # The request nonce is no longer needed. Leaving it set would let a
            # later link be spent from this browser without a fresh request.
            # HttpOnly so a script cannot read it. SameSite=Lax so another site
            # cannot send a POST here with the user's cookie attached.
            self.send_response(303)
            self.send_header("Location", "/")
            # No `Secure` flag. This server speaks plain HTTP, so `Secure`
            # would stop the browser sending the cookie and nobody could sign
            # in. HttpOnly and SameSite=Lax are set. The session cookie
            # therefore crosses the LAN in clear. The fix for that is TLS in
            # front of this port. Setting `Secure` without TLS would only
            # break sign-in.
            self.send_header("Set-Cookie",
                             f"sid={sid}; HttpOnly; SameSite=Lax; Path=/")
            self.send_header("Set-Cookie",
                             "req=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/")
            self.end_headers()
            return

        session = self._session()
        if not session:
            # `sent` is a flag, not a message: nothing from the URL is
            # rendered to somebody who is not signed in.
            self._send(401, portal.render_signin(query.get("sent") == ["1"]))
            return

        if parsed.path == "/logout":
            portal.end_session(portal.cookie_value(self.headers.get("Cookie", ""), "sid"))
            self._redirect("/", {"Set-Cookie":
                                 "sid=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/"})
            return

        if parsed.path in ("/connectors", "/capabilities"):
            self._send(200, portal.render_connectors(
                session["identity"], session["role"], flash,
                session.get("origin", portal.ORIGIN_AGENT)))
            return

        if parsed.path in ("/tasks", "/projects"):
            self._send(200, portal.agentbox_portal_engine.render_tasks(
                session["identity"], session["role"], flash,
                session.get("origin", portal.ORIGIN_AGENT)))
            return

        if parsed.path in ("/calendar", "/schedule"):
            self._send(200, portal.agentbox_portal_calendar.render_calendar(
                session["identity"], session["role"], flash,
                session.get("origin", portal.ORIGIN_AGENT)))
            return

        if parsed.path == "/knowledge":
            self._send(200, portal.render_knowledge(
                session["identity"], session["role"], flash,
                session.get("origin", portal.ORIGIN_AGENT)))
            return

        if parsed.path == "/google/callback":
            self._google_callback(session, query)
            return

        if parsed.path == "/admin":
            # Both gates take the origin. Without it the default would apply
            # and an assistant-made link could read Operations.
            why = portal.refusal(session["role"], "ops:read_health",
                          session.get("origin", portal.ORIGIN_AGENT))
            if why:
                self._send(403, portal.page("Not allowed",
                                     f"<h1>Not allowed</h1>"
                                     f"<p>{html.escape(portal.flash_text(why))}</p>"))
                return
            self._send(200, portal.render_admin(
                session["identity"], flash,
                session.get("origin", portal.ORIGIN_AGENT)))
            return

        if parsed.path in ("/skills", "/engine", "/settings"):
            portal.agentbox_portal_engine.dispatch_get(self, session, parsed.path, flash)
            return

        if parsed.path in ("/", "/inbox"):
            self._send(200, portal.render_home(
                session["identity"], session["role"], flash,
                session.get("origin", portal.ORIGIN_AGENT)))
            return
        self._send(404, portal.page("Not found", "<h1>Not found</h1>"))


    def _read_form(self) -> dict | None:
        """The request body, or None if the request is not well formed.

        Runs before authentication on the most privileged web page here, so it
        must not raise. A bad Content-Length would otherwise kill the handler
        and make a malformed request distinguishable from a valid one. The
        length is also capped, not trusted.

        Every failure gets the same answer, so the client is not told which
        part was wrong.
        """
        raw = (self.headers.get("Content-Length") or "0").strip() or "0"
        # ASCII digits only. `int()` accepts other numeral systems, and the
        # standard says this header is digits.
        if not (raw.isascii() and raw.isdigit()):
            return None
        length = int(raw)
        if length > portal.MAX_BODY_BYTES:
            return None
        try:
            body = self.rfile.read(length)
        except OSError:
            return None
        try:
            return urllib.parse.parse_qs(body.decode())
        except UnicodeDecodeError:
            return None

    def do_POST(self) -> None:  # noqa: N802
        form = self._read_form()
        if form is None:
            self._send(400, portal.page("No", "<h1>That request was malformed.</h1>"))
            return
        path = urllib.parse.urlparse(self.path).path

        if path == "/request":
            self._request_link((form.get("email") or [""])[0])
            return

        # Before the session gate: this endpoint *creates* sessions, and it
        # authenticates with its own token rather than a cookie.
        if path == "/agent/link":
            self._agent_link(form)
            return

        if path == "/agent/propose-invite":
            self._propose_invite(form)
            return

        session = self._session()
        if not session:
            self._send(401, portal.render_signin())
            return

        if path == "/chat/pair":
            # Withheld for the same reason as unlinking. Pairing decides where
            # this person's sign-in links are delivered, and the code it mints
            # is shown on a page the assistant can read whenever it holds a
            # link it produced. Unlinking only removes a channel. Pairing
            # points one somewhere new, so it is the more important of the two
            # to keep behind a link the person asked for themselves.
            if not portal.can(session["role"], "connector:pair_chat",
                       session.get("origin", portal.ORIGIN_AGENT)):
                self._redirect("/connectors?m=" + portal.origin_refusal(
                    session.get("origin", portal.ORIGIN_AGENT), "pair"))
                return
            portal.start_pairing(session["identity"])
            self._redirect("/connectors?m=pairing_started")
            return

        if path == "/chat/unlink":
            # Withheld from a link the assistant produced, like disconnecting.
            # Only the person may change where their sign-in links arrive.
            if not portal.can(session["role"], "connector:disconnect_own",
                       session.get("origin", portal.ORIGIN_AGENT)):
                self._redirect("/connectors?m=" + portal.origin_refusal(
                    session.get("origin", portal.ORIGIN_AGENT), "disconnect"))
                return
            portal.unlink_chat(session["identity"])
            self._redirect("/connectors?m=chat_unlinked")
            return

        if path == "/connectors/start":
            self._connector_action(session, form)
            return

        if path == "/admin/invite":
            self._invite(session, form)
            return

        if path == "/admin/onboard":
            self._onboard(session, form)
            return

        if path == "/admin/proposal":
            self._proposal(session, form)
            return

        if path == "/admin/invite-create":
            self._create_invite(session, form)
            return

        if path == "/admin/settings":
            self._save_settings(session, form)
            return

        if path.startswith("/tasks/"):
            portal.agentbox_portal_engine.dispatch_post(self, session, path, form)
            return

        if path.startswith("/engine/"):
            portal.agentbox_portal_engine.dispatch_post(self, session, path, form)
            return

        if path == "/admin/household":
            self._save_household(session, form)
            return

        if path == "/memory/forget":
            try:
                _, message = portal.forget_memory(
                    session["identity"], session["role"],
                    (form.get("id") or [""])[0],
                    session.get("origin", portal.ORIGIN_AGENT))
            except PermissionError:
                message = portal.origin_refusal(
                    session.get("origin", portal.ORIGIN_AGENT), "forget")
            self._redirect(f"/?m={urllib.parse.quote(message)}")
            return

        if path == "/memory/decide":
            try:
                verb = (form.get("verb") or [""])[0]
                _, message = portal.decide_memory(
                    session["identity"], session["role"],
                    (form.get("id") or [""])[0],
                    verb if verb in ("approve", "feedback") else "reject",
                    session.get("origin", portal.ORIGIN_AGENT),
                    statement=(form.get("statement") or [""])[0])
            except PermissionError:
                message = portal.origin_refusal(
                    session.get("origin", portal.ORIGIN_AGENT), "decide")
            self._redirect(f"/?m={urllib.parse.quote(message)}")
            return
        self._send(404, portal.page("Not found", "<h1>Not found</h1>"))
