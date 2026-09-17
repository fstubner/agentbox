"""agentbox_portal_views — self-service portal HTML views.

Follows the established agentbox modular CLI pattern:
- Dedicated module with clean functional boundaries (< 400 LOC)
- Preserves exact markup, escaping, form targets, and test compatibility
- Professional vector SVG icons instead of colored emojis
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

def _icon(name: str) -> str:
    if _portal and hasattr(_portal, "agentbox_portal_style"):
        return getattr(_portal.agentbox_portal_style, name, "")
    try:
        import cli.agentbox_portal_style as s
        return getattr(s, name, "")
    except Exception:
        return ""

def page(title: str, body: str) -> bytes:
    content = body if "<div class=app-layout>" in body else f"<div class=shell>{body}</div>"
    return (f"<!doctype html><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{get_style()}</style>"
            f"{content}").encode()

def chrome(identity: str, role: str, origin: str, active: str,
           body: str) -> str:
    """Modern Left Sidebar Workspace shell."""
    can_admin = _portal.can(role, "ops:read_health", origin) if _portal else False

    waiting = 0
    try:
        if _portal and hasattr(_portal, "own_proposals"):
            waiting = len(_portal.own_proposals(identity, role))
    except Exception:
        pass

    inbox_badge = f"<span class=nav-badge>{waiting}</span>" if waiting > 0 else ""

    nav_items = [
        ("/", "Inbox", _icon("ICON_INBOX"), inbox_badge, active in ("/", "/inbox")),
        ("/tasks", "Tasks", _icon("ICON_TASKS"), "", active in ("/tasks", "/projects")),
        ("/calendar", "Calendar", _icon("ICON_CALENDAR"), "", active in ("/calendar", "/schedule")),
        ("/knowledge", "Knowledge Base", _icon("ICON_KNOWLEDGE"), "", active == "/knowledge"),
        ("/capabilities", "Capabilities", _icon("ICON_CAPABILITIES"), "",
         active in ("/capabilities", "/connectors", "/skills")),
    ]
    if can_admin:
        nav_items.append(("/admin", "Operations", _icon("ICON_OPERATIONS"), "",
                          active in ("/admin", "/settings", "/engine")))

    rendered_nav = "".join(
        f"<a href='{href}' class='nav-item {'on' if is_on else ''}'>"
        f"{icon}<span>{html.escape(label)}</span>{badge}</a>"
        for href, label, icon, badge, is_on in nav_items
    )

    badge = ""
    if origin == _portal.ORIGIN_AGENT:
        badge = "<span class=badge>assistant</span>"

    initial = (identity[:1] or "U").upper()
    user_footer = (
        f"<div class=user-profile-row><div class=avatar-circle>{html.escape(initial)}</div>"
        f"<div class=user-details><div class=user-name-text>{html.escape(identity)}</div>"
        f"<div class=user-role-tag>{html.escape(role)}{badge}</div></div>"
        f"<a href='/logout' class=signout-link title='Sign out'>{_icon('ICON_LOGOUT')}</a></div>"
    )

    return (
        f"<div class=app-layout><aside class=sidebar>"
        f"<div class=brand-row>{_icon('ICON_BRAND')}"
        f"<span class=brand-title><a href='/'>Agentbox</a></span>"
        f"<span class=env-badge>Workspace</span></div>"
        f"<div class=sidebar-section><div class=section-label>Workspace</div>"
        f"<nav class=nav-menu>{rendered_nav}</nav></div>"
        f"<div class=sidebar-footer>{user_footer}</div></aside>"
        f"<main class=main-content>{body}</main></div>"
    )

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
            f"{waiting} memor{'ies' if waiting != 1 else 'y'} waiting for you</span></div>")

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
            f"{len(requests)} account change waiting on the household admin</span></div>")

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

def _render_proposals_list(proposals: list[dict], reachable: bool, identity: str) -> str:
    if not reachable:
        return ("<p class=empty><b>The memory service is not responding.</b> "
                "This is not the same as having nothing waiting — there may be "
                "proposals here that cannot be shown. Tell whoever runs this box.</p>")
    if not proposals:
        return ("<div class=empty>"
                "<div style='font-size:1.2rem;margin-bottom:.3rem;color:#22c55e'>&#10003;</div>"
                "<b>All clear — Inbox Zero</b>"
                "<p class=sub style='margin:.3rem 0 0'>The assistant proposes a memory "
                "when it notices something worth keeping; it cannot save one until you say yes.</p></div>")

    parts = []
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
                    "properly, instead of storing a note that works around it.</p>")
        parts.append(
            f"<div class=card><span class=scope>{html.escape(label)}</span>{hint}"
            f"<form method=post action=/memory/decide>"
            f"<input type=hidden name=id value='{html.escape(str(item.get('id')))}'>"
            f"<textarea name=statement rows=3 class=statement-box "
            f"placeholder='Proposed memory wording'>{html.escape(statement)}</textarea>"
            f"<p class=sub style='margin:.25rem 0 .65rem;font-size:.82rem'>"
            f"Edit before saving if it is not quite right.</p>"
            f"<button class=yes name=verb value=approve>"
            f"{'Save as a memory' if is_feedback else 'Remember this'}</button>"
            f"<button name=verb value=feedback>Not a memory — file as feedback</button>"
            f"<button class=danger name=verb value=reject "
            f"onclick=\"return confirm('Forget this memory proposal?');\">Forget it</button>"
            f"</form></div>")
    return "".join(parts)

def _render_memories_list(current: list[dict], history: dict, identity: str) -> str:
    if not current:
        return ""
    parts = ["<h2 style='margin-top:2rem'>What I remember</h2>"]
    for item in current:
        scope = html.escape(str(item.get("scope", "household")))
        label = "private to you" if scope == identity else scope
        scope_attr = "private" if scope == identity else "household"
        past = history.get(item["id"], [])
        chain = ""
        if past:
            rows = "".join(
                f"<div style='color:var(--muted);font-size:.85rem;padding:.2rem 0'>"
                f"was: {html.escape(str(p.get('statement','')))}</div>" for p in past)
            chain = (f"<details style='margin:.4rem 0 0'>"
                     f"<summary style='cursor:pointer;color:var(--muted);font-size:.85rem'>"
                     f"{len(past)} earlier version{'s' if len(past) > 1 else ''}</summary>"
                     f"{rows}</details>")
        parts.append(
            f"<div class=card data-scope='{scope_attr}'><span class=scope>{html.escape(label)}</span>"
            f"<p class=stmt>{html.escape(str(item.get('statement','')))}</p>{chain}"
            f"<form method=post action=/memory/forget>"
            f"<input type=hidden name=id value='{html.escape(str(item.get('id')))}'>"
            "<button class=danger onclick=\"return confirm('Permanently forget this memory?');\">"
            "Forget this</button></form></div>")
    return "".join(parts)

def render_home(identity: str, role: str, flash: str,
                origin: str = "email") -> bytes:
    try:
        proposals, reachable = _portal.own_proposals(identity, role), True
    except _portal.BridgeUnreachable:
        proposals, reachable = [], False
    try:
        stored, history = _portal.stored_memories(identity, role)
    except _portal.BridgeUnreachable:
        stored, history = [], {}

    parts = ["<h1>Inbox</h1>",
             "<p class=sub>Items and memory proposals waiting for your review.</p>",
             render_overview(identity, role, len(proposals), bool(stored))]

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
            "needs a link opened in the same browser that asked for it — "
            "that is what proves the person opening it is the person who asked, "
            "rather than anyone who saw the message.</p>"
            "<p class=sub style='margin:0'>Phone apps usually open links in "
            "their own browser, so this is normal. To get full access, request "
            "a link below and open it without leaving this browser.</p>"
            "<form method=post action=/request style='margin-top:.6rem'>"
            "<input type=hidden name=email value=''>"
            "<button class=yes>Send me a link for this browser</button></form></div>")

    if flash:
        parts.append(f"<div class=flash>{html.escape(flash)}</div>")

    parts.append("<h2>Pending Review</h2>")
    parts.append(_render_proposals_list(proposals, reachable, identity))
    parts.append(_render_memories_list(stored, history, identity))

    parts.append("<footer>Only you can approve a memory in your own private "
                 "scope — not the assistant, and not the household admin."
                 "</footer>")
    return page(f"{identity} — Agentbox",
                chrome(identity, role, origin, "/", "".join(parts)))

def render_knowledge(identity: str, role: str, flash: str,
                     origin: str = "email") -> bytes:
    """Dedicated Knowledge Base view with search and scope filters."""
    try:
        stored, history = _portal.stored_memories(identity, role)
    except _portal.BridgeUnreachable:
        stored, history = [], {}

    parts = [
        "<h1>Knowledge Base</h1>",
        "<p class=sub>Searchable permanent facts, household preferences, and version history.</p>",
    ]
    if flash:
        parts.append(f"<div class=flash>{html.escape(flash)}</div>")

    js_code = (
        "function filterMem(q){var t=q.toLowerCase();document.querySelectorAll('.mem-card')"
        ".forEach(function(c){var mt=c.textContent.toLowerCase().indexOf(t)!==-1;"
        "var sc=c.getAttribute('data-scope');var ms=(window._sc||'all')==='all'||sc===window._sc;"
        "c.style.display=(mt&&ms)?'':'none';});}"
        "function filterSc(s){window._sc=s;document.querySelectorAll('.filter-btn')"
        ".forEach(function(b){b.classList.remove('active');});"
        "var b=document.getElementById('btn-'+s);if(b)b.classList.add('active');"
        "var i=document.getElementById('filter-input');filterMem(i?i.value:'');}"
    )
    parts.append(
        "<input type=search id=filter-input class=search-bar placeholder='Search memories...' "
        "oninput='filterMem(this.value)'>"
        "<div class=filter-group>"
        "<button class='filter-btn active' id=btn-all onclick=\"filterSc('all')\">All</button>"
        "<button class='filter-btn' id=btn-household onclick=\"filterSc('household')\">Household</button>"
        "<button class='filter-btn' id=btn-private onclick=\"filterSc('private')\">Private</button>"
        f"</div><script>{js_code}</script>"
    )

    if not stored:
        parts.append("<div class=empty>No memories recorded yet. "
                     "When you approve proposals in the Inbox, they are stored here.</div>")
    else:
        parts.append("<h2 style='margin-top:1.5rem'>What I remember</h2>")
        for item in stored:
            scope = html.escape(str(item.get("scope", "household")))
            label = "private to you" if scope == identity else scope
            scope_attr = "private" if scope == identity else "household"
            past = history.get(item["id"], [])
            chain = ""
            if past:
                rows = "".join(
                    f"<div style='color:var(--muted);font-size:.85rem;padding:.2rem 0'>"
                    f"was: {html.escape(str(p.get('statement','')))}</div>" for p in past)
                chain = (f"<details style='margin:.4rem 0 0'>"
                         f"<summary style='cursor:pointer;color:var(--muted);font-size:.85rem'>"
                         f"{len(past)} earlier version{'s' if len(past) > 1 else ''}</summary>"
                         f"{rows}</details>")
            parts.append(
                f"<div class='card mem-card' data-scope='{scope_attr}'>"
                f"<span class=scope>{html.escape(label)}</span>"
                f"<p class=stmt>{html.escape(str(item.get('statement','')))}</p>{chain}"
                f"<form method=post action=/memory/forget>"
                f"<input type=hidden name=id value='{html.escape(str(item.get('id')))}'>"
            "<button class=danger onclick=\"return confirm('Permanently forget this memory?');\">"
            "Forget this</button></form></div>")

    # footer signout removed
    return page("Knowledge Base — Agentbox",
                chrome(identity, role, origin, "/knowledge", "".join(parts)))

SIGNIN = """<div class=card style="max-width:28rem;margin:3.5rem auto 0;padding:2rem 2.25rem">
<h1>Agentbox</h1>
<p class=sub>Sign in with your email address.</p>
{flash}
<form method=post action=/request>
  <p><input type=email name=email required placeholder="you@example.com"
     style="width:100%;padding:.65rem .75rem;font-size:1rem;margin-bottom:.6rem"></p>
  <button class=yes type=submit style="width:100%;padding:.6rem">Email me a link</button>
</form>
<footer style="margin-top:1.5rem">The link works once and expires in {minutes} minutes. Opened in this
browser it gives you full access; opened anywhere else you can read but not
change anything.</footer></div>"""

def render_signin(sent: bool = False) -> bytes:
    told = ("If that address belongs to someone here, a sign-in link is on "
            "its way. Open it in this browser for full access — opened "
            "anywhere else it can read but not change anything.")
    return page("Sign in — Agentbox", SIGNIN.format(
        minutes=_portal.LINK_TTL_SECONDS // 60,
        flash=f"<div class=flash>{html.escape(told)}</div>" if sent else ""))

def render_connectors(identity: str, role: str, flash: str,
                      origin: str = "email") -> bytes:
    """Renders the Capabilities & Integrations surface."""
    if _portal and hasattr(_portal, "agentbox_portal_engine"):
        return _portal.agentbox_portal_engine.render_capabilities(
            identity, role, flash, origin)
    return page("Capabilities — Agentbox",
                chrome(identity, role, origin, "/capabilities", "<h1>Capabilities</h1>"))

def _ago(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"
