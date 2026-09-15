"""Tests for the agentbox CLI: policy enforcement and repo validation."""
from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest
from conftest import code_of

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


def test_the_tool_schema_budget_is_enforced_and_current():
    """A comment in mcp_base put this cost at ~2,250 tokens per turn. By
    2026-08-14 it measured 7,778 — 3.5x, drifted silently while the project
    maintained a document on context economy. Nothing measured it."""
    import importlib.machinery
    import importlib.util
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    loader = importlib.machinery.SourceFileLoader("abx_budget", str(repo / "cli" / "agentbox"))
    spec = importlib.util.spec_from_loader("abx_budget", loader)
    cli = importlib.util.module_from_spec(spec)
    sys.modules["abx_budget"] = cli
    spec.loader.exec_module(cli)

    count, tokens, worst = cli.tool_schema_cost()
    assert count > 0, "could not assemble the tool surface"
    assert tokens <= cli.TOOL_SCHEMA_TOKEN_BUDGET, (
        f"{tokens} tokens over budget; biggest: {worst}")
    # And the stale figure is gone from the shared base.
    base = code_of(repo / "services/templates/mcp/mcp_base.py")
    assert "~2,250 tokens per turn" not in base


def test_validate_gates_on_lint(monkeypatch):
    """Lint is a gate, not advice.

    A NameError in the Google bridge was on screen from `ruff check` and got
    deployed anyway, crash-looping the container that holds the OAuth
    credential. validate runs before every deploy, so that is where a
    known-bad change has to stop.
    """
    from conftest import code_of
    source = code_of("cli/agentbox")
    block = source.split("def validate(")[1].split("\ndef ")[0]
    assert 'shutil.which("ruff")' in block
    assert '"ruff", "check"' in block
    # An absent linter warns rather than passing silently.
    assert "ruff not installed" in block


def test_a_missing_linter_does_not_read_as_a_pass():
    """A check that cannot run must not masquerade as one that passed."""
    from conftest import code_of
    block = code_of("cli/agentbox").split("def validate(")[1].split("\ndef ")[0]
    lint = block[block.index('shutil.which("ruff")'):]
    assert "ruff not installed" in lint
    assert "report(WARN" in lint


def test_floating_tags_are_reported_as_they_age():
    """A floating tag does not float.

    Every third-party image here is pinned to `stable`, `latest`, or no tag,
    and `deploy` runs `docker compose up -d --build` — which rebuilds what
    this repo builds and reuses whatever is cached for everything else. Home
    Assistant sat three weeks behind and Vikunja four months, and nothing
    reported either: doctor's staleness check compares source SHAs for images
    built here and had no notion of upstream ones.
    """
    from conftest import code_of
    source = code_of("cli/agentbox")
    assert "def third_party_images(" in source
    assert "IMAGE_STALE_DAYS" in source
    # Reported in doctor, not just available as a function.
    doctor = source.split("def doctor(")[1].split("\ndef ")[0]
    assert "third_party_images()" in doctor


def test_update_is_separate_from_deploy():
    """`deploy` must ship your change and nothing else.

    If it silently pulled upstream too, a one-line config fix could also jump
    Home Assistant a minor version, and a failure afterwards would have two
    candidate causes instead of one.
    """
    from conftest import code_of
    source = code_of("cli/agentbox")
    assert "def update(service: str)" in source
    deploy = source.split("def deploy(service:")[1].split("\ndef ")[0]
    # deploy only pulls when explicitly asked
    assert "pull: bool = False" in source.split("def deploy(service:")[1][:120]
    assert "if pull:" in deploy


def test_pull_reuses_deploys_secret_resolution():
    """A `docker compose pull` run without it dies interpolating secrets out
    of compose.yaml — which is exactly what a duplicated invocation got wrong
    here the first time."""
    from conftest import code_of
    source = code_of("cli/agentbox")
    update = source.split("def update(service: str)")[1].split("\ndef ")[0]
    # update delegates rather than building its own compose command
    assert "deploy(service, pull=True)" in update
    assert "docker" not in update


def test_update_resolves_service_aliases():
    """`cli/agentbox update home-assistant` must resolve to the real service
    directory `services/compose/homeassistant` rather than failing."""
    from conftest import code_of
    source = code_of("cli/agentbox")
    assert "SERVICE_ALIASES" in source
    assert '"home-assistant": "homeassistant"' in source
    assert "SERVICE_ALIASES.get(service, service)" in source


def test_third_party_images_reports_compose_service_name():
    """Doctor's update hint must name the actual compose service directory,
    not guess from the image tag where hyphens differ."""
    from conftest import code_of
    source = code_of("cli/agentbox")
    doctor = source.split("def doctor(")[1].split("\ndef ")[0]
    assert "Update: cli/agentbox update {service}" in doctor


def test_identity_list_answers_can_this_person_sign_in(monkeypatch, capsys):
    """A household is not a list of names: it is who may do what, and whether
    each person can actually get in. Those four facts lived in four
    environment variables across two systemd units, so answering "can Sam sign
    in?" meant looking in four places and reasoning about it.
    """
    import importlib.machinery
    import importlib.util
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    loader = importlib.machinery.SourceFileLoader("abx_ident", str(repo / "cli" / "agentbox"))
    spec = importlib.util.spec_from_loader("abx_ident", loader)
    cli = importlib.util.module_from_spec(spec)
    sys.modules["abx_ident"] = cli
    spec.loader.exec_module(cli)

    monkeypatch.setattr(cli, "read_env_file",
                        lambda p: {"AGENTBOX_IDENTITIES": "alex:a,sam:b,sam:c",
                                   "GOOGLE_BRIDGE_TOKEN_ALEX": "t"})
    monkeypatch.setattr(cli, "unit_environment", lambda unit: {
        "agentbox-portal": {"AGENTBOX_ADMINS": "alex",
                            "AGENTBOX_IDENTITY_EMAILS": "alex:f@e.com",
                            "AGENTBOX_SMTP_HOST": "smtp.example.com"},
        "agentbox-approvals": {"AGENTBOX_DISCORD_IDENTITIES": "sam:999"},
    }.get(unit, {}))

    assert cli.identity_list() == 0
    out = capsys.readouterr().out
    assert "alex      admin" in out
    assert "sam        member" in out
    # Each person's actual route in, not a global claim about SMTP.
    assert "email" in out and "Discord DM" in out
    # And the one who has neither is named rather than left to be discovered.
    assert "sam cannot request a link themselves" in out


def test_identity_list_does_not_invent_a_route(monkeypatch, capsys):
    """SMTP configured but no address for that person is not a way in."""
    import importlib.machinery
    import importlib.util
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    loader = importlib.machinery.SourceFileLoader("abx_ident2", str(repo / "cli" / "agentbox"))
    spec = importlib.util.spec_from_loader("abx_ident2", loader)
    cli = importlib.util.module_from_spec(spec)
    sys.modules["abx_ident2"] = cli
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "read_env_file",
                        lambda p: {"AGENTBOX_IDENTITIES": "sam:b"})
    monkeypatch.setattr(cli, "unit_environment", lambda unit: {
        "AGENTBOX_SMTP_HOST": "smtp.example.com"} if "portal" in unit else {})
    cli.identity_list()
    out = capsys.readouterr().out
    assert "operator link only" in out
    assert "sam cannot request a link themselves" in out
