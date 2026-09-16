"""agentbox_portal_engine — capabilities, skills catalog, and engine controls.

Follows the established agentbox modular CLI pattern:
- Dedicated module with clean functional boundaries (< 400 LOC)
- Dynamic discovery of platform and personal skills
- Hardware envelope reporting and 1-click crash-consistent DR snapshots
"""
from __future__ import annotations

import html
import os
import shutil
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
    name = skill_dir.name
    desc = ""
    tags = []
    try:
        content = skill_md.read_text(encoding="utf-8", errors="replace")
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                for line in parts[1].splitlines():
                    line = line.strip()
                    if line.startswith("name:"):
                        name = line.split("name:", 1)[1].strip().strip('"\'')
                    elif line.startswith("description:"):
                        desc = line.split("description:", 1)[1].strip().strip('"\'')
                    elif line.startswith("tags:"):
                        raw_tags = line.split("tags:", 1)[1].strip().strip("[]")
                        tags = [t.strip().strip('"\'') for t in raw_tags.split(",") if t.strip()]
        if not desc:
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith(("#", "---")):
                    desc = line
                    break
    except Exception:
        desc = "Error loading skill metadata"

    return {
        "name": name,
        "desc": desc or "No description provided.",
        "tags": tags,
        "origin": origin,
        "path": str(skill_dir),
    }


def load_all_skills() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Scans for both personal and platform skills."""
    platform_dir = Path("/home/alex/oss/agentbox/skills")
    personal_dir = Path("/home/alex/oss/agentbox-personal/skills")

    platform_skills = []
    if platform_dir.is_dir():
        for d in sorted(platform_dir.iterdir()):
            if d.is_dir():
                s = _parse_skill(d, "platform")
                if s:
                    platform_skills.append(s)

    personal_skills = []
    if personal_dir.is_dir():
        for d in sorted(personal_dir.iterdir()):
            if d.is_dir():
                s = _parse_skill(d, "personal")
                if s:
                    personal_skills.append(s)

    return personal_skills, platform_skills


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
    parts.append("<h2>Connected Accounts</h2>")
    parts.append("<p class=sub>What Agentbox can reach on your behalf.</p>")

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

    for s in personal:
        tag_badges = "".join(f"<span class=badge style='font-size:.65rem'>{html.escape(t)}</span>"
                             for t in s["tags"])
        parts.append(
            f"<div class=card style='margin-bottom:.75rem'>"
            f"<div style='display:flex;align-items:center;gap:.5rem;margin-bottom:.35rem'>"
            f"<b>{html.escape(s['name'])}</b><span class=scope style='margin:0'>personal</span>"
            f"{tag_badges}</div>"
            f"<p class=sub style='margin:0 0 .4rem'>{html.escape(s['desc'])}</p>"
            f"<div class=when>Path: <code>{html.escape(s['path'])}</code></div>"
            f"</div>"
        )

    for s in platform:
        tag_badges = "".join(f"<span class=badge style='font-size:.65rem'>{html.escape(t)}</span>"
                             for t in s["tags"])
        parts.append(
            f"<div class=card style='margin-bottom:.75rem'>"
            f"<div style='display:flex;align-items:center;gap:.5rem;margin-bottom:.35rem'>"
            f"<b>{html.escape(s['name'])}</b>"
            f"<span class=badge style='margin:0'>platform</span>"
            f"{tag_badges}</div>"
            f"<p class=sub style='margin:0 0 .4rem'>{html.escape(s['desc'])}</p>"
            f"<div class=when>Path: <code>{html.escape(s['path'])}</code></div>"
            f"</div>"
        )

    parts.append("<footer><a href='/logout'>Sign out</a></footer>")
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

    parts.append("<footer><a href='/logout'>Sign out</a></footer>")
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
