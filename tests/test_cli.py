"""Tests for the agentbox CLI: policy enforcement and repo validation."""
from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

CLI_PATH = Path(__file__).resolve().parent.parent / "cli" / "agentbox"


@pytest.fixture()
def cli(monkeypatch):
    monkeypatch.setenv("AGENTBOX_REPO", str(Path(__file__).resolve().parent.parent))
    loader = SourceFileLoader("agentbox_cli", str(CLI_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_policy_allowed_action(cli, capsys):
    code = cli.policy_check("read_repo_files")
    assert code == 0
    assert '"tier": "allowed"' in capsys.readouterr().out


def test_policy_approval_required_action(cli, capsys):
    code = cli.policy_check("spend_money")
    assert code == 0
    assert '"tier": "approval_required"' in capsys.readouterr().out


def test_policy_always_denied_action_is_blocked(cli, capsys):
    """The core enforcement guarantee: a denied action never returns success."""
    code = cli.policy_check("merge_own_pr")
    out = capsys.readouterr().out
    assert code == 2, "always_denied actions must return a non-zero, distinct exit code"
    assert '"tier": "always_denied"' in out


def test_policy_unknown_action_defaults_to_approval_required(cli, capsys):
    """Deny-by-default: anything not explicitly allowed requires approval."""
    code = cli.policy_check("some_action_nobody_declared")
    assert code == 0
    assert '"tier": "approval_required"' in capsys.readouterr().out


def test_load_policy_has_all_tiers(cli):
    tiers = cli.load_policy()
    assert set(tiers) == {"allowed", "approval_required", "always_denied"}
    assert tiers["allowed"] and tiers["approval_required"] and tiers["always_denied"]


def test_validate_passes_on_clean_repo(monkeypatch, cli):
    # validate's own rules only — docker's compose parser is a separate
    # concern with its own version skew, and `deploy` is where a broken file
    # actually has to be caught.
    monkeypatch.setenv("AGENTBOX_VALIDATE_SKIP_COMPOSE", "1")
    assert cli.validate() == 0


def test_doctor_says_when_a_service_is_stopped_rather_than_wedged(monkeypatch):
    """"not responding" covers two conditions needing opposite responses.

    The router was cleanly stopped twice in two days — signal TERM, so
    Restart=on-failure never applied — and the only symptom was triage_email
    reporting "router unreachable", which reads like a network fault rather
    than a service somebody turned off.
    """
    import importlib.machinery
    import importlib.util
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    loader = importlib.machinery.SourceFileLoader("abx_hint", str(repo / "cli" / "agentbox"))
    spec = importlib.util.spec_from_loader("abx_hint", loader)
    cli = importlib.util.module_from_spec(spec)
    sys.modules["abx_hint"] = cli
    spec.loader.exec_module(cli)

    def fake(cmd, **kwargs):
        state = "inactive" if "agentbox-router" in cmd else "active"
        return type("R", (), {"stdout": state + "\n", "returncode": 0})()

    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(cli.subprocess, "run", fake)
    hint = cli.stopped_hint("router")
    assert "inactive" in hint
    assert "systemctl --user start agentbox-router" in hint
    # A running-but-unreachable service needs the opposite diagnosis.
    assert "wedged" in cli.stopped_hint("portal")
    # An endpoint with no unit we can name says nothing rather than guessing.
    assert cli.stopped_hint("main model") == ""
