"""agentbox_portal_views — user-facing views, dashboard shell, and styles.

Follows the established agentbox modular CLI pattern:
- Dedicated module with clean functional boundaries (< 400 LOC)
- Preserves exact HTML templates, CSS styles, badge logic, and error handling
"""
from __future__ import annotations

import html
from typing import Any

_portal: Any = None


def bind(portal_mod: Any) -> None:
    global _portal
    _portal = portal_mod


STYLE = """
 *{box-sizing:border-box}
 body{font:15px/1.55 system-ui,-apple-system,sans-serif;margin:0;color:#1a1d21;
      background:#f6f7f9}
 .shell{max-width:56rem;margin:0 auto;padding:0 1rem 3rem}
 header.top{background:#fff;border-bottom:1px solid #e3e6ea;margin-bottom:1.5rem}
 header.top .shell{display:flex;align-items:center;gap:1rem;padding:.85rem 1rem}
 .brand{font-weight:650;letter-spacing:-.01em}
 .who{margin-left:auto;color:#5b6470;font-size:.875rem}
 .badge{display:inline-block;font-size:.7rem;text-transform:uppercase;
        letter-spacing:.04em;background:#eef1f5;color:#4a5563;border-radius:1rem;
        padding:.15rem .5rem;margin-left:.4rem}
 .tabs{display:flex;gap:.25rem;border-bottom:1px solid #e3e6ea;margin:0 0 1.5rem}
 .tabs a{padding:.6rem .9rem;text-decoration:none;color:#5b6470;
         border-bottom:2px solid transparent;font-size:.925rem}
 .tabs a.on{color:#1a1d21;border-bottom-color:#2f6feb;font-weight:550}
 h1{font-size:1.25rem;margin:0 0 .2rem;letter-spacing:-.01em}
 .card{background:#fff;border:1px solid #e3e6ea;border-radius:.6rem;
       padding:1rem 1.1rem;margin:0 0 .75rem}
 .warn{background:#fff8e6;border-color:#f0d99a}
 .empty{color:#5b6470;background:#fff;border:1px dashed #d7dce2;
        border-radius:.6rem;padding:1.75rem 1.1rem;text-align:center}
 .row{display:flex;align-items:center;gap:.75rem;flex-wrap:wrap}
 .grow{flex:1;min-width:12rem}
 .dot{width:.5rem;height:.5rem;border-radius:50%;display:inline-block;
      margin-right:.4rem}
 .dot.on{background:#1a7f37}.dot.off{background:#b9c0c9}

 .app-switch{display:flex;align-items:center;gap:.35rem;margin-left:1.2rem}
 .app-switch a{font-size:.78rem;color:#5b6470;text-decoration:none;padding:.15rem .45rem;
               border-radius:.3rem;background:#f0f2f5;border:1px solid #e3e6ea}
 .app-switch a:hover{background:#e2e6eb;color:#1a1d21}
 button.danger{color:#cf222e;border-color:#d0d7de;background:#fff}
 button.danger:hover{background:#cf222e;color:#fff;border-color:#cf222e}
 .scope{display:inline-block;font-size:.75rem;background:#eef;color:#334;
        border-radius:.25rem;padding:.1rem .4rem;margin-bottom:.4rem}
 .stmt{margin:0 0 .75rem}
 h2{font-size:1.05rem;margin:1.6rem 0 .2rem;letter-spacing:-.01em}
 .row{display:flex;align-items:baseline;gap:.5rem;padding:.2rem 0}
 .row .name{flex:1}
 .dot{width:.55rem;height:.55rem;border-radius:50%;flex:none;
      display:inline-block;position:relative;top:-.1rem}
 .dot.ok{background:#2e7d32} .dot.warn{background:#c77700}
 .dot.fail{background:#c62828}
 .when{color:#888;font-size:.8rem}
 button{font:inherit;padding:.45rem .9rem;border-radius:.35rem;cursor:pointer;
        border:1px solid #bbb;background:#fff;margin-right:.4rem}
 button.yes{background:#1a7f37;border-color:#1a7f37;color:#fff}
 .flash{background:#eef7ee;border:1px solid #cde3cd;padding:.6rem .8rem;
        border-radius:.35rem;margin:0 0 1rem}
 .empty{color:#666}
 nav a{margin-right:1rem}
 footer{margin-top:2.5rem;color:#888;font-size:.85rem}
"""


def page(title: str, body: str) -> bytes:
    return (f"<!doctype html><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{STYLE}</style>"
            f"<div class=shell>{body}</div>").encode()


def chrome(identity: str, role: str, origin: str, active: str,
           body: str) -> str:
    """The dashboard shell: identity bar, tabs, then the page.

    The origin badge is not decoration. A person needs to know why an approve
    button is missing, and "this link came from the assistant" is the answer.
    """
    tabs = [("/", "Memories"), ("/connectors", "Accounts")]
    # Offered only when this session could actually open it. An admin on an
    # assistant-minted link is an admin whose link cannot read Operations, and
    # a tab that always 403s teaches people the nav lies.
    if _portal.can(role, "ops:read_health", origin):
        tabs.append(("/admin", "Operations"))
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
    return (f"<header class=top><div class=shell>"
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
            f"<textarea name=statement rows=3 style='width:100%;padding:.5rem;"
            f"font:inherit;border:1px solid #ccd2d9;border-radius:.35rem;"
            f"resize:vertical'>{html.escape(statement)}</textarea>"
            f"<p class=sub style='margin:.35rem 0 .6rem;font-size:.8rem'>"
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
        flash=f"<div class=flash>{told}</div>" if sent else ""))


def render_connectors(identity: str, role: str, flash: str,
                      origin: str = "email") -> bytes:
    parts = ["<h1>Accounts</h1>",
             "<p class=sub>What Agentbox can reach on your behalf.</p>"]
    if flash:
        parts.append(f"<div class=flash>{html.escape(flash)}</div>")
    waiting = _portal.pending_requests(identity)
    if waiting:
        kinds = ", ".join(sorted({str(r.get("action", "?")) for r in waiting}))
        parts.append(
            f"<div class=card><b>Waiting for the household admin</b>"
            f"<p class=sub style='margin:.4rem 0 0'>You asked to {html.escape(kinds)}. "
            f"Someone with operator access has to finish it — that step needs a "
            f"credential this page deliberately does not hold.</p></div>")

    for connector in _portal.connector_status(identity):
        state = "Connected" if connector["connected"] else "Not connected"
        parts.append(
            f"<div class=card><b>{html.escape(connector['name'])}</b>"
            f"<p class=sub style='margin:.3rem 0 .8rem'>{state} "
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
        body = ("<p class=sub style='margin:.3rem 0 .8rem'>Connected "
                "\u2014 sign-in links come to you on Discord.</p>"
                "<form method=post action=/chat/unlink style='display:inline'>"
                "<button class=danger "
                "onclick=\"return confirm('Disconnect Discord identity?');\">Disconnect Discord</button></form>")
    elif pending:
        body = (f"<p class=sub style='margin:.3rem 0 .6rem'>Send this to the "
                f"Agentbox bot on Discord, as a direct message:</p>"
                f"<div style='font-family:{'ui-monospace,monospace'};"
                f"font-size:1.35rem;letter-spacing:.12em;padding:.5rem .7rem;"
                f"background:#eef1f5;border-radius:.35rem;display:inline-block'>"
                f"link {html.escape(pending[0])}</div>"
                f"<p class=sub style='margin:.6rem 0 0'>It expires in "
                f"{_portal.PAIRING_TTL_SECONDS // 60} minutes. Sending it from your "
                f"account is what proves it is yours \u2014 anyone can type a "
                f"username into a form.</p>")
    else:
        body = ("<p class=sub style='margin:.3rem 0 .8rem'>Not connected. "
                "Connect it and you can ask for your own sign-in links "
                "instead of someone handing you one.</p>"
                "<form method=post action=/chat/pair style='display:inline'>"
                "<button class=yes>Connect Discord</button></form>")
    parts.append(f"<div class=card><b>Discord</b>{body}</div>")

    parts.append(
        "<footer>Reconnecting replaces the stored credential. Disconnecting "
        "revokes it at Google and removes it from this box \u2014 your memories "
        "are untouched either way."
        "<br><a href='/logout'>Sign out</a></footer>")
    return page("Accounts \u2014 Agentbox",
                chrome(identity, role, origin, "/connectors", "".join(parts)))


def _ago(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"
