"""Skills the assistant has: the repository's own, plus a household's private
ones when AGENTBOX_PERSONAL_PATH points at them."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


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
        "tags": tags, "origin": origin, "path": f"{origin}/skills/{skill_dir.name}",
    }


def load_all_skills() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    def _scan(p: Path, o: str) -> list[dict[str, Any]]:
        return [s for d in sorted(p.iterdir()) if d.is_dir() and (s := _parse_skill(d, o))] if p.is_dir() else []
    base = Path(__file__).resolve().parent.parent
    # A household's own skills live in a separate private checkout. There is
    # no default location, so nothing private is read unless it is configured.
    personal_path = os.environ.get("AGENTBOX_PERSONAL_PATH", "").strip()
    personal = _scan(Path(personal_path) / "skills", "personal") if personal_path else []
    platform = _scan(base / "skills", "platform")
    return personal, platform
