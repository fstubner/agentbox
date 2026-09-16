"""Skills catalog and Engine/Disaster-Recovery views for Agentbox Console.

Enables inspecting active platform and personal skills, reviewing local model
capacity, and triggering crash-consistent backups and restore verification.
"""

from __future__ import annotations

import html
import os
import re
import shutil
from pathlib import Path
from typing import Any

_portal: Any = None


def bind(portal_mod: Any) -> None:
    global _portal
    _portal = portal_mod


def _parse_skill(skill_dir: Path, scope: str) -> dict[str, Any] | None:
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return None
    try:
        text = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    name = skill_dir.name
    desc = ""
    tags = []
    category = "general"

    # Extract YAML frontmatter if present
    match = re.search(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if match:
        fm = match.group(1)
        name_m = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
        if name_m:
            name = name_m.group(1).strip()
        desc_m = re.search(r"^description:\s*(.+)$", fm, re.MULTILINE)
        if desc_m:
            desc = desc_m.group(1).strip()
        tags_m = re.search(r"tags:\s*\[(.*?)\]", fm)
        if tags_m:
            tags = [t.strip() for t in tags_m.group(1).split(",") if t.strip()]
        cat_m = re.search(r"category:\s*(.+)$", fm, re.MULTILINE)
        if cat_m:
            category = cat_m.group(1).strip()
    else:
        first_line = text.strip().splitlines()[0] if text.strip() else ""
        desc = first_line.lstrip("#").strip()

    return {
        "name": name,
        "desc": desc,
        "scope": scope,
        "tags": tags,
        "category": category,
        "path": str(skill_dir),
    }


def load_all_skills() -> tuple[list[dict], list[dict]]:
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


def render_skills(identity: str, role: str, flash: str = "",
                  origin: str = "email") -> bytes:
    parts = [
        "<h1>Skills & Capabilities</h1>",
        "<p class=sub>Active reasoning skills and tool suites loaded into your assistant.</p>",
    ]
    if flash:
        parts.append(f"<div class=flash>{html.escape(flash)}</div>")

    personal, platform = load_all_skills()

    parts.append("<h2>Household Personal Skills</h2>")
    parts.append("<p class=sub>Custom household rules and domain skills from your private repository.</p>")
    if not personal:
        parts.append("<p class=empty>No personal skills discovered in <code>agentbox-personal</code>.</p>")
    else:
        for s in personal:
            tag_badges = "".join(
                f"<span class=badge style='font-size:.65rem'>{html.escape(t)}</span>"
                for t in s["tags"])
            parts.append(
                f"<div class=card style='margin-bottom:.75rem'>"
                f"<div style='display:flex;align-items:center;gap:.5rem;margin-bottom:.35rem'>"
                f"<b>{html.escape(s['name'])}</b>"
                f"<span class=scope style='margin:0'>personal</span>"
                f"{tag_badges}"
                f"</div>"
                f"<p class=sub style='margin:0 0 .4rem'>{html.escape(s['desc'])}</p>"
                f"<div class=when>Path: <code>{html.escape(s['path'])}</code></div>"
                f"</div>"
            )

    parts.append("<h2 style='margin-top:2rem'>Platform System Skills</h2>")
    parts.append("<p class=sub>Core operational skills and architectural safeguards.</p>")
    for s in platform:
        tag_badges = "".join(
            f"<span class=badge style='font-size:.65rem'>{html.escape(t)}</span>"
            for t in s["tags"])
        parts.append(
            f"<div class=card style='margin-bottom:.75rem'>"
            f"<div style='display:flex;align-items:center;gap:.5rem;margin-bottom:.35rem'>"
            f"<b>{html.escape(s['name'])}</b>"
            f"<span class=badge style='margin:0;background:#f0f2f5'>platform</span>"
            f"{tag_badges}"
            f"</div>"
            f"<p class=sub style='margin:0 0 .4rem'>{html.escape(s['desc'])}</p>"
            f"<div class=when>Path: <code>{html.escape(s['path'])}</code></div>"
            f"</div>"
        )

    parts.append("<footer><a href='/logout'>Sign out</a></footer>")
    return _portal.page("Skills — Agentbox",
                        _portal.chrome(identity, role, origin, "/skills", "".join(parts)))


def _latest_backup() -> tuple[str, str]:
    backup_dir = Path(os.path.expanduser("~/.local/state/agentbox/backups"))
    if not backup_dir.is_dir():
        return "None recorded", "0 KB"
    archives = sorted(backup_dir.glob("agentbox-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not archives:
        return "None recorded", "0 KB"
    latest = archives[0]
    size_kb = latest.stat().st_size // 1024
    return latest.name, f"{size_kb:,} KB"


def render_engine(identity: str, role: str, flash: str = "",
                  origin: str = "email") -> bytes:
    parts = [
        "<h1>Engine & Disaster Recovery</h1>",
        "<p class=sub>Local inference runtime, memory boundaries, and full-state disaster recovery snapshots.</p>",
    ]
    if flash:
        parts.append(f"<div class=flash>{html.escape(flash)}</div>")

    # Disk & System Capacity
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
        f"<span class=when><a href='http://127.0.0.1:4321' target=_blank>eval workbench</a></span></div>"
        "</div>"
    )

    # Disaster Recovery Snapshot Center
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

    if path == "/skills":
        handler._send(200, render_skills(
            session["identity"], session["role"], flash,
            session.get("origin", _portal.ORIGIN_AGENT)))
        return
    if path == "/engine":
        handler._send(200, render_engine(
            session["identity"], session["role"], flash,
            session.get("origin", _portal.ORIGIN_AGENT)))
        return
    if path == "/admin":
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
