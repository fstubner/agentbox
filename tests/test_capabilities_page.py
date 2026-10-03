"""The Capabilities page shows only what is configured, and only what is true."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from conftest import code_of

ENGINE = Path(__file__).resolve().parents[1] / "cli" / "agentbox_portal_engine.py"


def _engine():
    spec = importlib.util.spec_from_file_location("engine_under_test", ENGINE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_no_private_skills_are_read_unless_configured(monkeypatch):
    """Private skills are a household's own business. Guessing a location
    would put them on a page for anyone running this code."""
    monkeypatch.delenv("AGENTBOX_PERSONAL_PATH", raising=False)
    import agentbox_skills
    personal, platform = agentbox_skills.load_all_skills()
    assert personal == []
    assert platform, "the repository's own skills should still be listed"


def test_configured_skills_show_a_relative_path(tmp_path, monkeypatch):
    skill = tmp_path / "skills" / "meal-planner"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: meal-planner\ndescription: Plan meals.\n---\n", encoding="utf-8")
    monkeypatch.setenv("AGENTBOX_PERSONAL_PATH", str(tmp_path))
    import agentbox_skills
    personal, _ = agentbox_skills.load_all_skills()
    assert [s["path"] for s in personal] == ["personal/skills/meal-planner"]
    assert str(tmp_path) not in repr(personal)


def test_service_status_is_probed_not_assumed():
    """Both cards once showed a green dot whatever the service was doing."""
    source = code_of(ENGINE)
    assert "class='dot ok'" not in source
    assert "agentbox_status.probe(" in source


def test_links_use_the_portal_host_not_a_fixed_address():
    cli = ENGINE.parent
    for name in ("agentbox_portal_engine.py", "agentbox_portal_calendar.py"):
        source = code_of(cli / name)
        assert "service_url(" in source, name
        assert "http://192." not in source and "agentbox.local:" not in source, name
