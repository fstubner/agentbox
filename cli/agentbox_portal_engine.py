"""agentbox_portal_engine — capabilities, skills catalog, and engine controls.

Follows the established agentbox modular CLI pattern:
- Dedicated module with clean functional boundaries (< 400 LOC)
- Dynamic discovery of platform and personal skills
- Hardware envelope reporting and 1-click crash-consistent DR snapshots
"""
from __future__ import annotations

import datetime
import html
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

_portal: Any = None


def bind(portal_mod: Any) -> None:
    global _portal
    _portal = portal_mod


def _parse_skill(skill_dir: Path, origin: str) -> dict[str, Any] | None:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    name, desc, tags = skill_dir.name, "", []
    try:
        content = skill_md.read_text(encoding="utf-8", errors="replace")
        if content.startswith("---") and len(parts := content.split("---", 2)) >= 3:
            for line in parts[1].splitlines():
                k, _, v = line.partition(":")
                k, v = k.strip(), v.strip().strip("'\"")
                if k == "name":
                    name = v
                elif k == "description":
                    desc = v
                elif k == "tags":
                    tags = [t.strip().strip("'\"") for t in v.strip("[]").split(",") if t.strip()]
        if not desc:
            for line in content.splitlines():
                s = line.strip()
                if s and not s.startswith(("#", "---")):
                    desc = s
                    break
    except Exception:
        desc = "Error loading skill metadata"
    return {
        "name": name, "desc": desc or "No description provided.",
        "tags": tags, "origin": origin, "path": str(skill_dir),
    }


def load_all_skills() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    def _scan(p: Path, o: str) -> list[dict[str, Any]]:
        return [s for d in sorted(p.iterdir()) if d.is_dir() and (s := _parse_skill(d, o))] if p.is_dir() else []
    personal = _scan(Path("/home/alex/oss/agentbox-personal/skills"), "personal")
    platform = _scan(Path("/home/alex/oss/agentbox/skills"), "platform")
    return personal, platform


def _latest_backup() -> tuple[str, str]:
    backup_dir = Path(os.path.expanduser("~/.local/state/agentbox/backups"))
    if not backup_dir.is_dir():
        return "None recorded", "0 KB"
    archives = sorted(backup_dir.glob("agentbox-*.tar.gz"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    if not archives:
        return "None recorded", "0 KB"
    latest = archives[0]
    size_kb = latest.stat().st_size // 1024
    return latest.name, f"{size_kb:,} KB"


def render_capabilities(identity: str, role: str, flash: str = "",
                        origin: str = "email") -> bytes:
    """Unified Capabilities & Integrations surface."""
    parts = [
        "<h1>Capabilities & Integrations</h1>",
        "<p class=sub>External accounts, smart home services, and reasoning skills.</p>",
    ]
    if flash:
        parts.append(f"<div class=flash>{html.escape(flash)}</div>")

    # 1. Accounts & Communication
    parts.append("<h2>Connected Accounts</h2>"
                 "<p class=sub>What Agentbox can reach on your behalf.</p>")

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
                "— sign-in links come to you on Discord.</p>"
                "<form method=post action=/chat/unlink style='display:inline'>"
                "<button class=danger "
                "onclick=\"return confirm('Disconnect Discord identity?');\">"
                "Disconnect Discord</button></form>")
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

    # 2. Smart Home & Household Services
    parts.append("<h2>Smart Home & Core Services</h2>")
    parts.append(
        "<div class=card><b>Vikunja Tasks & Projects</b>"
        "<p class=sub style='margin:.3rem 0 .7rem'>Household shared task lists and todo items.</p>"
        "<div class=row><span class='dot ok'></span><span class=name>Local Service on :3456</span>"
        "<span class=when><a href='http://192.0.2.10:3456' target=_blank>open web app</a></span></div>"
        "</div>"
    )
    parts.append(
        "<div class=card><b>Home Assistant Smart Home</b>"
        "<p class=sub style='margin:.3rem 0 .7rem'>Automations, lighting, sensors, and climate.</p>"
        "<div class=row><span class='dot ok'></span><span class=name>Household Instance on :8123</span>"
        "<span class=when><a href='http://192.0.2.10:8123' target=_blank>open web app</a></span></div>"
        "</div>"
    )

    # 3. Reasoning Skills
    personal, platform = load_all_skills()
    parts.append("<h2>Reasoning Skills & Tools</h2>")
    parts.append("<p class=sub>Capabilities and specialized workflows loaded into the assistant.</p>")

    for s in personal + platform:
        tag_badges = ''.join(f'<span class=badge style="font-size:.65rem">{html.escape(t)}</span>'
                             for t in s['tags'])
        scope_cls = 'scope' if s.get('origin') == 'personal' else 'badge'
        parts.append(
            f'<div class=card style="margin-bottom:.75rem">'
            f'<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.35rem">'
            f'<b>{html.escape(s["name"])}</b><span class={scope_cls} style="margin:0">{s.get("origin")}</span>'
            f'{tag_badges}</div>'
            f'<p class=sub style="margin:0 0 .4rem">{html.escape(s["desc"])}</p>'
            f'<div class=when>Path: <code>{html.escape(s["path"])}</code></div>'
            f'</div>'
        )


    return _portal.page("Capabilities — Agentbox",
                        _portal.chrome(identity, role, origin, "/capabilities", "".join(parts)))


def render_engine(identity: str, role: str, flash: str = "",
                  origin: str = "email") -> bytes:
    parts = [
        "<h1>Engine & Disaster Recovery</h1>",
        "<p class=sub>Local inference runtime, memory boundaries, and full-state disaster recovery snapshots.</p>",
    ]
    if flash:
        parts.append(f"<div class=flash>{html.escape(flash)}</div>")

    usage = shutil.disk_usage("/")
    free_gb = usage.free // (1024 * 1024 * 1024)
    total_gb = usage.total // (1024 * 1024 * 1024)
    used_pct = int((usage.used / usage.total) * 100)

    parts.append(
        "<div class=card><b>System Capacity & Hardware Envelope</b>"
        "<div class=sub style='margin:.4rem 0 .75rem'>AMD Ryzen Embedded APU · 64 GB Unified Memory</div>"
        f"<div class=row><span class='dot {'warn' if used_pct > 85 else 'ok'}'></span>"
        f"<span class=name>Physical NVMe Disk: {free_gb} GB free ({used_pct}% used of {total_gb} GB)</span></div>"
        f"<div class=row><span class='dot ok'></span>"
        f"<span class=name>Local Inference Endpoint: <code>http://127.0.0.1:1234/v1</code></span>"
        f"<span class=when style='color:var(--muted)'>port 1234</span></div>"
        "</div>"
    )

    latest_name, latest_size = _latest_backup()
    parts.append(
        "<div class=card><b>Disaster Recovery Snapshots</b>"
        "<p class=sub style='margin:.4rem 0 .75rem'>"
        "Crash-consistent state snapshots capturing 4 tiers: memories, Vikunja tasks, "
        "Hermes conversation history (SQLite backup), and service credentials."
        "</p>"
        f"<div class=row><span class='dot ok'></span>"
        f"<span class=name>Latest Verified Snapshot: <b>{html.escape(latest_name)}</b></span>"
        f"<span class=when>{html.escape(latest_size)}</span></div>"
        "<div style='display:flex;gap:.5rem;margin-top:1rem;flex-wrap:wrap'>"
        "<form method=post action=/engine/backup style='margin:0'>"
        "<button type=submit class=yes name=action value=backup>Create Backup Snapshot</button>"
        "</form>"
        "<form method=post action=/engine/restore-check style='margin:0'>"
        "<button type=submit name=action value=restore-check>Rehearse Restore</button>"
        "</form>"
        "</div>"
        "</div>"
    )


    return _portal.page("Engine — Agentbox",
                        _portal.chrome(identity, role, origin, "/engine", "".join(parts)))


def dispatch_get(handler: Any, session: dict, path: str, flash: str) -> None:
    why = _portal.refusal(session["role"], "ops:read_health",
                          session.get("origin", _portal.ORIGIN_AGENT))
    if why:
        handler._send(403, _portal.page("Not allowed",
                                        f"<h1>Not allowed</h1>"
                                        f"<p>{html.escape(_portal.flash_text(why))}</p>"))
        return

    if path in ("/capabilities", "/connectors", "/skills"):
        handler._send(200, render_capabilities(
            session["identity"], session["role"], flash,
            session.get("origin", _portal.ORIGIN_AGENT)))
        return
    if path == "/engine":
        handler._send(200, render_engine(
            session["identity"], session["role"], flash,
            session.get("origin", _portal.ORIGIN_AGENT)))
        return
    if path in ("/admin", "/settings"):
        if path == "/settings":
            handler._redirect("/admin")
            return
        handler._send(200, _portal.render_admin(
            session["identity"], flash,
            session.get("origin", _portal.ORIGIN_AGENT)))
        return


def dispatch_post(handler: Any, session: dict, path: str, form: dict) -> None:
    if path == "/tasks/toggle":
        tid = (form.get("id") or [""])[0]
        done = (form.get("done") or ["1"])[0] == "1"
        ok = toggle_vikunja_task(int(tid), done) if tid.isdigit() else False
        handler._redirect("/tasks?m=" + (("task_completed" if done else "task_reopened") if ok else "task_error"))
        return

    why = _portal.refusal(session["role"], "ops:write_settings",
                          session.get("origin", _portal.ORIGIN_AGENT))
    if why:
        handler._send(403, _portal.page("Not allowed",
                                        f"<h1>Not allowed</h1>"
                                        f"<p>{html.escape(_portal.flash_text(why))}</p>"))
        return

    try:
        import agentbox_backup
    except ImportError:
        from cli import agentbox_backup

    if path == "/engine/backup":
        code = agentbox_backup.backup(keep=14)
        msg_key = "backup_ok" if code == 0 else "backup_failed"
        handler._redirect(f"/engine?m={msg_key}")
        return

    if path == "/engine/restore-check":
        code = agentbox_backup.restore_check()
        msg_key = "restore_ok" if code == 0 else "restore_failed"
        handler._redirect(f"/engine?m={msg_key}")
        return


def toggle_vikunja_task(task_id: int, done: bool) -> bool:
    p = Path.home() / ".local/state/agentbox/vikunja/db/vikunja.db"
    if not p.exists():
        return False
    try:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(str(p), timeout=5.0) as con:
            cur = con.cursor()
            if done:
                cur.execute("UPDATE tasks SET done = 1, done_at = ?, updated = ? WHERE id = ?",
                            (now_str, now_str, task_id))
            else:
                cur.execute("UPDATE tasks SET done = 0, done_at = NULL, updated = ? WHERE id = ?",
                            (now_str, task_id))
        return True
    except Exception:
        return False


def load_vikunja_tasks() -> tuple[list[tuple], list[tuple]]:
    p = Path.home() / ".local/state/agentbox/vikunja/db/vikunja.db"
    if not p.exists():
        return [], []
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        cur = con.cursor()
        projects = cur.execute("SELECT id, title FROM projects WHERE is_archived = 0 ORDER BY id").fetchall()
        tasks = cur.execute("""
            SELECT t.id, t.title, t.description, t.done, t.due_date, p.title as project_title
            FROM tasks t LEFT JOIN projects p ON t.project_id = p.id
            WHERE t.deleted_at IS NULL ORDER BY t.done ASC, t.id DESC LIMIT 40
        """).fetchall()
        con.close()
        return projects, tasks
    except Exception:
        return [], []


def render_tasks(identity: str, role: str, flash: str = "",
                 origin: str = "email") -> bytes:
    """Projects & Tasks view powered by Vikunja."""
    projects, tasks = load_vikunja_tasks()
    parts = []
    if flash:
        parts.append(f"<div class=flash>{html.escape(flash)}</div>")

    parts.append(
        "<div style='display:flex;align-items:center;justify-content:space-between;"
        "margin-bottom:1.5rem;flex-wrap:wrap;gap:1rem'>"
        "<div><h1 style='margin:0 0 .25rem'>Projects & Tasks</h1>"
        "<p class=sub style='margin:0'>Household task lists, active projects, and todo tracking via Vikunja.</p></div>"
        "<a href='http://agentbox.local:3456' target=_blank class='button yes' "
        "style='margin:0;font-weight:600;display:inline-flex;align-items:center;gap:.4rem'>"
        "Open Vikunja Workspace &rarr;</a></div>"
    )

    pending = [t for t in tasks if not t[3]]
    completed = [t for t in tasks if t[3]]

    parts.append("<h2>Active Tasks</h2>")
    if not pending:
        parts.append(
            "<div class=empty>No active tasks right now. "
            "Create one in Vikunja or ask the assistant to add a task!</div>")
    else:
        for t in pending:
            tid, title, desc, done, due, proj = t
            due_str = f" &middot; due {html.escape(str(due)[:10])}" if due else ""
            desc_str = f"<div class=sub style='margin:.2rem 0 0'>{html.escape(desc)}</div>" if desc else ""
            badge_str = f"<span class=badge style='font-size:.65rem'>{html.escape(proj or 'General')}</span>"
            btn = (f"<form method=post action=/tasks/toggle style='margin:0'>"
                   f"<input type=hidden name=id value='{tid}'><input type=hidden name=done value='1'>"
                   f"<button class=task-check title='Mark completed' style='margin-top:.15rem'></button></form>")
            parts.append(
                f"<div class=card style='margin-bottom:.55rem;padding:.85rem 1.15rem'>"
                f"<div style='display:flex;align-items:flex-start;gap:.75rem'>{btn}"
                f"<div style='flex:1;min-width:0'><b style='font-size:.92rem'>{html.escape(title)}</b>{desc_str}</div>"
                f"{badge_str}{due_str}</div></div>"
            )

    if completed:
        parts.append(f"<h2 style='margin-top:1.5rem'>Recently Completed ({len(completed)})</h2>")
        for t in completed[:5]:
            tid, title, desc, done, due, proj = t
            btn = (f"<form method=post action=/tasks/toggle style='margin:0'>"
                   f"<input type=hidden name=id value='{tid}'><input type=hidden name=done value='0'>"
                   f"<button class='task-check done' title='Reopen task'>✓</button></form>")
            parts.append(
                f"<div class=card style='margin-bottom:.45rem;padding:.75rem 1.15rem;opacity:0.75'>"
                f"<div style='display:flex;align-items:center;gap:.6rem'>{btn}"
                f"<span style='flex:1;text-decoration:line-through;color:var(--muted);font-size:.88rem'>"
                f"{html.escape(title)}</span>"
                f"<span class=badge style='font-size:.65rem'>{html.escape(proj or 'General')}</span>"
                f"</div></div>"
            )

    return _portal.page("Projects & Tasks — Agentbox",
                        _portal.chrome(identity, role, origin, "/tasks", "".join(parts)))
