"""The portal's calendar: scheduled tasks from Vikunja and events from Google
Calendar in one view, with tasks completable from the page.
"""
from __future__ import annotations

import datetime
import html
import sqlite3
from pathlib import Path
from typing import Any

_portal: Any = None


def bind(portal_mod: Any) -> None:
    global _portal
    _portal = portal_mod


def load_schedule() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load scheduled tasks from Vikunja and calendar events if available."""
    tasks = []
    p = Path.home() / ".local/state/agentbox/vikunja/db/vikunja.db"
    if p.exists():
        try:
            con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            con.execute("PRAGMA busy_timeout = 5000")
            cur = con.cursor()
            rows = cur.execute("""
                SELECT t.id, t.title, t.description, t.done, t.due_date, p.title as project_title
                FROM tasks t LEFT JOIN projects p ON t.project_id = p.id
                WHERE t.deleted_at IS NULL AND t.done = 0
                ORDER BY CASE WHEN t.due_date IS NULL OR t.due_date = '' THEN 1 ELSE 0 END, t.due_date ASC, t.id DESC
                LIMIT 50
            """).fetchall()
            con.close()
            for r in rows:
                tasks.append({
                    "id": r[0],
                    "title": r[1],
                    "description": r[2] or "",
                    "done": bool(r[3]),
                    "due_date": r[4],
                    "project": r[5] or "General",
                })
        except Exception:
            pass

    events: list[dict[str, Any]] = []
    return tasks, events


def render_calendar(identity: str, role: str, flash: str = "",
                    origin: str = "email") -> bytes:
    """Render the unified household calendar and schedule."""
    tasks, events = load_schedule()
    parts = []
    if flash:
        parts.append(f"<div class=flash>{html.escape(flash)}</div>")

    parts.append(
        "<div style='display:flex;align-items:center;justify-content:space-between;"
        "margin-bottom:1.5rem;flex-wrap:wrap;gap:1rem'>"
        "<div><h1 style='margin:0 0 .25rem'>Household Schedule & Calendar</h1>"
        "<p class=sub style='margin:0'>Unified timeline of family commitments, "
        "deadlines, and scheduled tasks.</p></div>"
        "<div style='display:flex;gap:.5rem;align-items:center'>"
        f"<a href='{html.escape(_portal.service_url(3456))}' target=_blank class='button' "
        "style='margin:0;display:inline-flex;align-items:center;gap:.4rem'>"
        "Vikunja Workspace &rarr;</a>"
        "<a href='https://calendar.google.com' target=_blank class='button' "
        "style='margin:0;display:inline-flex;align-items:center;gap:.4rem'>"
        "Google Calendar &rarr;</a>"
        "</div></div>"
    )

    now = datetime.datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    # Group tasks by due date
    dated_tasks = [t for t in tasks if t.get("due_date")]
    undated_tasks = [t for t in tasks if not t.get("due_date")]

    parts.append("<div style='display:grid;grid-template-columns:repeat(auto-fit, minmax(320px, 1fr));gap:1.25rem'>")

    # Left Column: Scheduled Timeline & Deadlines
    parts.append("<div><h2>Upcoming Deadlines</h2>")
    if not dated_tasks:
        parts.append(
            "<div class=card style='padding:1.25rem;text-align:center;color:var(--muted)'>"
            "<p style='margin:0 0 .5rem'>No time-sensitive deadlines scheduled this week.</p>"
            "<span class=sub>Set a due date on a Vikunja task or ask the assistant: "
            "<i>'Remind me to submit bills by Friday'</i></span></div>"
        )
    else:
        for t in dated_tasks:
            tid = t["id"]
            title = html.escape(t["title"])
            desc = (f"<div class=sub style='margin:.2rem 0 0'>{html.escape(t['description'])}</div>"
                    if t["description"] else "")
            proj = html.escape(t["project"])
            due = str(t["due_date"])[:10]
            is_today = due == today_str
            due_badge = f"<span class='badge {'yes' if is_today else ''}' style='font-size:.68rem'>Due {due}</span>"
            btn = (f"<form method=post action=/tasks/toggle style='margin:0'>"
                   f"<input type=hidden name=id value='{tid}'><input type=hidden name=done value='1'>"
                   f"<button class=task-check title='Mark completed' style='margin-top:.15rem'></button></form>")
            parts.append(
                f"<div class=card style='margin-bottom:.55rem;padding:.85rem 1.15rem'>"
                f"<div style='display:flex;align-items:flex-start;gap:.75rem'>{btn}"
                f"<div style='flex:1;min-width:0'><b style='font-size:.92rem'>{title}</b>{desc}</div>"
                f"<div style='display:flex;flex-direction:column;align-items:flex-end;gap:.25rem'>"
                f"{due_badge}<span class=badge style='font-size:.65rem'>{proj}</span></div></div></div>"
            )

    parts.append("</div>")

    # Right Column: Undated Household Action Items
    parts.append("<div><h2>Action Queue (Undated)</h2>")
    if not undated_tasks:
        parts.append(
            "<div class=card style='padding:1.25rem;text-align:center;color:var(--muted)'>"
            "<p style='margin:0'>All active tasks have scheduled deadlines!</p></div>"
        )
    else:
        for t in undated_tasks[:10]:
            tid = t["id"]
            title = html.escape(t["title"])
            desc = (f"<div class=sub style='margin:.2rem 0 0'>{html.escape(t['description'])}</div>"
                    if t["description"] else "")
            proj = html.escape(t["project"])
            btn = (f"<form method=post action=/tasks/toggle style='margin:0'>"
                   f"<input type=hidden name=id value='{tid}'><input type=hidden name=done value='1'>"
                   f"<button class=task-check title='Mark completed' style='margin-top:.15rem'></button></form>")
            parts.append(
                f"<div class=card style='margin-bottom:.55rem;padding:.85rem 1.15rem'>"
                f"<div style='display:flex;align-items:flex-start;gap:.75rem'>{btn}"
                f"<div style='flex:1;min-width:0'><b style='font-size:.92rem'>{title}</b>{desc}</div>"
                f"<span class=badge style='font-size:.65rem'>{proj}</span></div></div>"
            )

    parts.append("</div></div>")

    return _portal.page("Calendar & Schedule · Agentbox",
                        _portal.chrome(identity, role, origin, "/calendar", "".join(parts)))
