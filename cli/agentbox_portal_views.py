"""agentbox_portal_views — self-service portal HTML views.

Follows the established agentbox modular CLI pattern:
- Dedicated module with clean functional boundaries (< 400 LOC)
- Preserves exact markup, escaping, form targets, and test compatibility
"""
from __future__ import annotations

import html
from typing import Any

_portal: Any = None


def bind(portal_mod: Any) -> None:
    global _portal
    _portal = portal_mod


def get_style() -> str:
    if _portal and hasattr(_portal, "agentbox_portal_style"):
        return _portal.agentbox_portal_style.STYLE
    try:
        from cli.agentbox_portal_style import STYLE
        return STYLE
    except ImportError:
        return ""


STYLE = get_style()


def page(title: str, body: str) -> bytes:
    content = body if "<header class=top>" in body else f"<div class=shell>{body}</div>"
    return (f"<!doctype html><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{get_style()}</style>"
            f"{content}").encode()


def chrome(identity: str, role: str, origin: str, active: str,
           body: str) -> str:
    """The dashboard shell: identity bar, tabs, then the page.

    The origin badge is not decoration. A person needs to know why an approve
    button is missing, and 'this link came from the assistant' is the answer.
    """
    tabs = [("/", "Memories"), ("/connectors", "Accounts")]
    if _portal.can(role, "ops:read_health", origin):
        tabs.append(("/skills", "Skills"))
        tabs.append(("/admin", "Operations"))
        tabs.append(("/engine", "Engine"))
    rendered = "".join(
        f"<a href='{href}' class='{'on' if href == active else ''}'>"
        f"{label}</a>" for href, label in tabs)
    badge = ""
    if origin == _portal.ORIGIN_AGENT:
        badge = "<span class=badge>assistant link</span>"
    elif role == _portal.ADMIN:
        badge = "<span class=badge>admin</span>"
    app_nav = (
        "<nav class=app-switch aria-label='Other interfaces'>"
        "<a href='http://127.0.0.1:4321' title='Control Plane Lab'>Control Plane</a>"
        "<a href='http://192.0.2.10:3456' target=_blank title='Tasks (Vikunja)'>Tasks</a>"
        "<a href='http://192.0.2.10:8123' target=_blank title='Home Assistant'>Home</a>"
        "</nav>"
    )
    return (f"<header class=top><div class=top-inner>"
            f"<span class=brand><a href='/' style='text-decoration:none;color:inherit'>Agentbox</a></span>"
            f"{app_nav}"
            f"<span class=who>{html.escape(identity)}{badge}</span>"
            f"</div></header><div class=shell>"
            f"<nav class=tabs>{rendered}</nav>{body}</div>")


def render_overview(identity: str, role: str, waiting: int,
                    has_memories: bool) -> str:
    """What this is, what needs you, and what it can reach."""
    parts = []
    if not has_memories and not waiting:
        parts.append(
            "<div class=card><b>What this is</b>"
            "<p class=sub style='margin:.4rem 0 0'>Agentbox is your "
            "household's own assistant, running on a box in your home. It can "
            "reach your mail, calendar, tasks and the house — and it "
            "remembers things about you, but only the ones you approve here. "
            "Nothing is saved because it decided to; every memory on this page "
            "is waiting for you to say yes.</p></div>")

    lines = []
    if waiting:
        lines.append(
            f"<div class=row><span class='dot warn'></span><span class=name>"
            f"{waiting} memor{'ies' if waiting != 1 else 'y'} waiting for you"
            f"</span></div>")

    connected = [c["name"] for c in _portal.connector_status(identity) if c["connected"]]
    chat = _portal.chat_account_for(identity)
    if connected or chat:
        reach = ", ".join(connected + (["Discord"] if chat else []))
        lines.append(
            f"<div class=row><span class='dot ok'></span><span class=name>"
            f"Connected: {html.escape(reach)}</span>"
            f"<span class=when><a href='/connectors'>manage</a></span></div>")
    else:
        lines.append(
            "<div class=row><span class='dot warn'></span><span class=name>"
            "No accounts connected yet</span>"
            "<span class=when><a href='/connectors'>set up</a></span></div>")

    requests = _portal.pending_requests(identity)
    if requests:
        lines.append(
            f"<div class=row><span class='dot warn'></span><span class=name>"
            f"{len(requests)} account change waiting on the household admin"
            f"</span></div>")

    if role == _portal.ADMIN:
        snap = _portal.agentbox_status.cached()
        if snap.failing:
            level, text = "fail", f"{len(snap.failing)} service(s) down"
        elif snap.problems or snap.stale_services:
            level, text = "warn", "Some things need attention"
        elif snap.evaluating:
            level, text = "ok", "Everything is running (evaluation in progress)"
        else:
            level, text = "ok", "Everything is running"
        lines.append(
            f"<div class=row><span class='dot {level}'></span>"
            f"<span class=name>{html.escape(text)}</span>"
            f"<span class=when><a href='/admin'>operations</a></span></div>")

    parts.append(f"<div class=card>{''.join(lines)}</div>")
    return "".join(parts)


def render_home(identity: str, role: str, flash: str,
                origin: str = "email") -> bytes:
    try:
        proposals, reachable = _portal.own_proposals(identity, role), True
    except _portal.BridgeUnreachable:
        proposals, reachable = [], False
    try:
        stored, _ = _portal.stored_memories(identity, role)
    except _portal.BridgeUnreachable:
        stored = []
    parts = ["<h1>Agentbox</h1>",
             f"<p class=sub>Hi {html.escape(identity)}.</p>",
             render_overview(identity, role, len(proposals), bool(stored)),
             "<h2>Memories</h2>",
             "<p class=sub>What Agentbox would like to remember about you. "
             "Nothing is saved until you say so.</p>"]
    if origin == _portal.ORIGIN_AGENT:
        parts.append(
            "<div class='card warn'><b>Signed in from an assistant link</b>"
            "<p class=sub style='margin:.35rem 0 0'>You can read everything "
            "here, but approving a memory needs a link you requested yourself. "
            "The assistant can see any link it sends you, so a link it made "
            "cannot be used to approve its own memories.</p></div>")
    elif origin == _portal.ORIGIN_CHAT:
        parts.append(
            "<div class='card warn'><b>Opened in a different browser</b>"
            "<p class=sub style='margin:.35rem 0 .5rem'>You can read "
            "everything here. Approving a memory or disconnecting an account "
            "needs a link opened in the same browser that asked for it \u2014 "
            "that is what proves the person opening it is the person who asked, "
            "rather than anyone who saw the message.</p>"
            "<p class=sub style='margin:0'>Phone apps usually open links in "
            "their own browser, so this is normal. To get full access, request "
            "a link below and open it without leaving this browser.</p>"
            "<form method=post action=/request style='margin-top:.6rem'>"
            "<input type=hidden name=email value=''>"
            "<button class=yes>Send me a link for this browser</button>"
            "</form></div>")
    if flash:
        parts.append(f"<div class=flash>{html.escape(flash)}</div>")

    if not reachable:
        parts.append("<p class=empty><b>The memory service is not "
                     "responding.</b> This is not the same as having nothing "
                     "waiting — there may be proposals here that cannot be "
                     "shown. Tell whoever runs this box.</p>")
    elif not proposals:
        parts.append("<p class=empty>Nothing waiting. The assistant proposes a "
                     "memory when it notices something worth keeping; it cannot "
                     "save one until you say yes.</div>")
    for item in proposals:
        scope = html.escape(str(item.get("scope", "household")))
        label = "private to you" if scope == identity else scope
        statement = str(item.get("statement", ""))
        is_feedback = item.get("kind") == "feedback"
        reason = str(item.get("kind_reason") or "")
        hint = ""
        if is_feedback:
            hint = ("<p class=sub style='margin:.1rem 0 .6rem'>This looks like "
                    "<b>feedback about how I behave</b>"
                    + (f" — {html.escape(reason)}" if reason else "")
                    + ". Filing it as feedback puts it on a list to fix "
                    "properly, instead of storing a note that works around "
                    "it.</p>")
        parts.append(
            f"<div class=card><span class=scope>{html.escape(label)}</span>"
            f"{hint}"
            f"<form method=post action=/memory/decide>"
            f"<input type=hidden name=id value='{html.escape(str(item.get('id')))}'>"
            f"<textarea name=statement rows=3 class=statement-box "
            f"placeholder='Proposed memory wording'>{html.escape(statement)}</textarea>"
            f"<p class=sub style='margin:.25rem 0 .65rem;font-size:.82rem'>"
            f"Edit before saving if it is not quite right.</p>"
            f"<button class=yes name=verb value=approve>"
            f"{'Save as a memory' if is_feedback else 'Remember this'}</button>"
            f"<button name=verb value=feedback>"
            f"{'Not a memory — file as feedback' if is_feedback else 'Not a memory — file as feedback'}"
            f"</button>"
            "<button class=danger name=verb value=reject "
            "onclick=\"return confirm('Forget this memory proposal?');\">Forget it</button>"
            f"</form></div>")
    try:
        current, history = _portal.stored_memories(identity, role)
    except _portal.BridgeUnreachable:
        current, history = [], {}
    if current:
        parts.append("<h1 style='margin-top:2rem'>What I remember</h1>")
        for item in current:
            scope = html.escape(str(item.get("scope", "household")))
            label = "private to you" if scope == identity else scope
            past = history.get(item["id"], [])
            chain = ""
            if past:
                rows = "".join(
                    f"<div style='color:#5b6470;font-size:.85rem;"
                    f"padding:.2rem 0'>was: {html.escape(str(p.get('statement','')))}"
                    f"</div>" for p in past)
                chain = (f"<details style='margin:.4rem 0 0'>"
                         f"<summary style='cursor:pointer;color:#5b6470;"
                         f"font-size:.85rem'>{len(past)} earlier version"
                         f"{'s' if len(past) > 1 else ''}</summary>{rows}"
                         f"</details>")
            parts.append(
                f"<div class=card><span class=scope>{html.escape(label)}</span>"
                f"<p class=stmt>{html.escape(str(item.get('statement','')))}</p>"
                f"{chain}"
                f"<form method=post action=/memory/forget>"
                f"<input type=hidden name=id value='{html.escape(str(item.get('id')))}'>"
                "<button class=danger "
                "onclick=\"return confirm('Permanently forget this memory?');\">Forget this</button></form></div>")

    parts.append("<footer>Only you can approve a memory in your own private "
                 "scope \u2014 not the assistant, and not the household admin."
                 " &middot; <a href='/logout'>Sign out</a></footer>")
    return page(f"{identity} \u2014 Agentbox",
                chrome(identity, role, origin, "/", "".join(parts)))


SIGNIN = """<h1>Agentbox</h1>
<p class=sub>Sign in with your email address.</p>
{flash}
<form method=post action=/request>
  <p><input type=email name=email required placeholder="you@example.com"
     style="width:100%;padding:.6rem;font-size:1rem;border:1px solid #bbb;
            border-radius:.35rem"></p>
  <button class=yes type=submit>Email me a link</button>
</form>
<footer>The link works once and expires in {minutes} minutes. Opened in this
browser it gives you full access; opened anywhere else you can read but not
change anything.</footer>"""


def render_signin(sent: bool = False) -> bytes:
    told = ("If that address belongs to someone here, a sign-in link is on "
            "its way. Open it in this browser for full access — opened "
            "anywhere else it can read but not change anything.")
    return page("Sign in \u2014 Agentbox", SIGNIN.format(
        minutes=_portal.LINK_TTL_SECONDS // 60,
        flash=f"<div class=flash>{html.escape(told)}</div>" if sent else ""))


def render_connectors(identity: str, role: str, flash: str,
                      origin: str = "email") -> bytes:
    parts = ["<h1>Accounts</h1>",
             "<p class=sub>What Agentbox can reach on your behalf.</p>"]
    if flash:
        parts.append(f"<div class=flash>{html.escape(flash)}</div>")
    for connector in _portal.connector_status(identity):
        if not connector.get("configured", True):
            continue
        parts.append(
            f"<div class=card><b>{html.escape(connector['name'])}</b>"
            f"<p class=sub style='margin:.3rem 0 .8rem'>"
            f"{'Connected' if connector['connected'] else 'Not connected'} "
            f"&mdash; {html.escape(connector['detail'])}.</p>"
            f"<form method=post action=/connectors/start style='display:inline'>"
            f"<input type=hidden name=connector value='{html.escape(connector['key'])}'>"
            f"<button class=yes name=action value=reconnect>"
            f"{'Reconnect or switch account' if connector['connected'] else 'Connect'}"
            f"</button>"
            + ("<button class=danger name=action value=disconnect "
               "onclick=\"return confirm('Disconnect this service?');\">Disconnect</button>"
               if connector["connected"] else "")
            + "</form></div>")

    account = _portal.chat_account_for(identity)
    pending = [code for code, rec in _portal.load_chat_links()["pending"].items()
               if rec.get("identity") == identity
               and int(rec.get("expires_at", 0)) > _portal.now()]
    if account:
        body = (f"<p class=sub style='margin:.3rem 0 .8rem'>"
                f"Connected as <b>{html.escape(account)}</b> "
                "\u2014 sign-in links come to you on Discord.</p>"
                "<form method=post action=/chat/unlink style='display:inline'>"
                "<button class=danger "
                "onclick=\"return confirm('Disconnect Discord identity?');\">Disconnect Discord</button></form>")
    elif pending:
        body = (f"<p class=sub style='margin:.3rem 0 .6rem'>Send this to the "
                f"Agentbox bot on Discord, as a direct message:</p>"
                f"<div style='font-family:monospace;font-size:1.35rem;"
                f"letter-spacing:.12em;padding:.5rem .7rem;"
                f"background:var(--input-bg);border-radius:.35rem;display:inline-block'>"
                f"link {html.escape(pending[0])}</div>"
                f"<p class=sub style='margin:.6rem 0 0'>Expires in "
                f"{_portal.PAIRING_TTL_SECONDS // 60} minutes.</p>")
    else:
        body = ("<p class=sub style='margin:.3rem 0 .8rem'>Not connected. "
                "Connect it and you can ask for your own sign-in links instead "
                "of someone handing you one.</p>"
                "<form method=post action=/chat/pair style='display:inline'>"
                "<button class=yes>Connect Discord</button></form>")
    parts.append(f"<div class=card><b>Discord</b>{body}</div>")

    parts.append("<footer>Reconnecting replaces the stored credential. "
                 "Disconnecting revokes it at Google and removes it from this "
                 "box \u2014 your memories are untouched either way.<br>"
                 "<a href='/logout'>Sign out</a></footer>")
    return page("Accounts \u2014 Agentbox",
                chrome(identity, role, origin, "/connectors", "".join(parts)))


def _ago(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"
