"""Tests for the agentbox CLI: policy enforcement and repo validation."""
from __future__ import annotations

import importlib.util
import re
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
    """A denied action never returns success."""
    code = cli.policy_check("merge_own_pr")
    out = capsys.readouterr().out
    assert code == 2, "always_denied actions must return a non-zero, distinct exit code"
    assert '"tier": "always_denied"' in out


def test_policy_unknown_action_defaults_to_approval_required(cli, capsys):
    """Deny by default. Anything not listed as allowed requires approval."""
    code = cli.policy_check("some_action_nobody_declared")
    assert code == 0
    assert '"tier": "approval_required"' in capsys.readouterr().out


def test_load_policy_has_all_tiers(cli):
    import agentbox_policy
    tiers = agentbox_policy.load_policy()
    assert set(tiers) == {"allowed", "approval_required", "always_denied"}
    assert tiers["allowed"] and tiers["approval_required"] and tiers["always_denied"]


def test_validate_passes_on_clean_repo(monkeypatch, cli):
    # validate's own rules only. Docker's compose parser varies by version,
    # and `deploy` is where a broken file is caught.
    monkeypatch.setenv("AGENTBOX_VALIDATE_SKIP_COMPOSE", "1")
    import agentbox_validate
    assert agentbox_validate.validate() == 0


def test_doctor_says_when_a_service_is_stopped_rather_than_wedged(monkeypatch):
    """"Not responding" can mean stopped or stuck, which need opposite fixes.

    A cleanly stopped unit is not restarted by Restart=on-failure, and it can
    look like a network fault, so doctor says when a service is stopped.
    """
    import subprocess

    import agentbox_doctor as cli

    # The portal is the example unit here. The router is retired, and the
    # difference between stopped and wedged applies to every remaining unit.
    state = {"value": "inactive"}

    def fake(cmd, **kwargs):
        return type("R", (), {"stdout": state["value"] + "\n", "returncode": 0})()

    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(cli.subprocess, "run", fake)
    hint = cli.stopped_hint("portal")
    assert "inactive" in hint
    assert "systemctl --user start agentbox-portal" in hint
    # A running-but-unreachable service needs the opposite diagnosis.
    state["value"] = "active"
    assert "wedged" in cli.stopped_hint("portal")
    # An endpoint with no unit we can name says nothing rather than guessing.
    assert cli.stopped_hint("main model") == ""


def test_the_tool_schema_budget_is_enforced_and_current():
    """The tool schemas are sent on every turn. Their cost is held to a budget
    so that growth fails validate."""
    import agentbox_validate as cli

    count, tokens, worst = cli.tool_schema_cost()
    assert count > 0, "could not assemble the tool surface"
    assert tokens <= cli.TOOL_SCHEMA_TOKEN_BUDGET, (
        f"{tokens} tokens over budget; biggest: {worst}")
    # The old hard-coded figure is not in the shared base.
    base = code_of("services/templates/mcp/mcp_base.py")
    assert "~2,250 tokens per turn" not in base


def test_validate_gates_on_lint(monkeypatch):
    """A lint failure fails validate.

    A NameError that `ruff check` reports can still be deployed if lint only
    warns, and in the Google bridge that crash-loops the container holding the
    OAuth credential. validate runs before every deploy, so it stops the
    change there.
    """
    from conftest import code_of
    source = code_of("cli/agentbox_validate.py")
    block = source.split("def validate(")[1].split("\ndef ")[0]
    assert 'shutil.which("ruff")' in block
    assert '"ruff", "check"' in block
    # A missing linter produces a warning, not a silent pass.
    assert "ruff not installed" in block


def test_a_missing_linter_does_not_read_as_a_pass():
    """A check that cannot run must not be reported as passed."""
    from conftest import code_of
    block = code_of("cli/agentbox_validate.py").split("def validate(")[1].split("\ndef ")[0]
    lint = block[block.index('shutil.which("ruff")'):]
    assert "ruff not installed" in lint
    assert "report(WARN" in lint


def test_floating_tags_are_reported_as_they_age():
    """A floating tag does not update by itself.

    Third-party images use tags such as `stable` or `latest`, and `deploy`
    reuses whatever is cached, so they stay at whatever was first pulled.
    doctor reports their age so `update` gets run.
    """
    from conftest import code_of
    source = code_of("cli/agentbox_doctor.py")
    assert "def third_party_images(" in source
    assert "IMAGE_STALE_DAYS" in source
    # Reported in doctor, not just available as a function.
    doctor = source.split("def doctor(")[1].split("\ndef ")[0]
    assert "third_party_images()" in doctor


def test_update_is_separate_from_deploy():
    """`deploy` must ship your change and nothing else.

    If deploy pulled upstream images, a one-line config fix could also move
    Home Assistant a minor version, and a failure afterwards would have two
    candidate causes instead of one.
    """
    from conftest import code_of
    source = code_of("cli/agentbox_deploy.py")
    assert "def update(service: str)" in source
    deploy = source.split("def deploy(service:")[1].split("\ndef ")[0]
    # deploy only pulls when explicitly asked
    assert "pull: bool = False" in source.split("def deploy(service:")[1][:120]
    assert "if pull:" in deploy


def test_pull_reuses_deploys_secret_resolution():
    """`docker compose pull` needs the same secret resolution as a deploy,
    because compose.yaml refers to secrets."""
    from conftest import code_of
    source = code_of("cli/agentbox_deploy.py")
    update = source.split("def update(service: str)")[1].split("\ndef ")[0]
    # update delegates rather than building its own compose command
    assert "deploy(service, pull=True)" in update
    assert "docker" not in update


def test_update_resolves_service_aliases():
    """`cli/agentbox update home-assistant` must resolve to the real service
    directory `services/compose/homeassistant` rather than failing."""
    from conftest import code_of
    source = code_of("cli/agentbox_deploy.py")
    assert "SERVICE_ALIASES" in source
    assert '"home-assistant": "homeassistant"' in source
    assert "SERVICE_ALIASES.get(service, service)" in source


def test_third_party_images_reports_compose_service_name():
    """Doctor's update hint must name the compose service directory, and
    not guess from the image tag where hyphens differ."""
    from conftest import code_of
    source = code_of("cli/agentbox_doctor.py")
    doctor = source.split("def doctor(")[1].split("\ndef ")[0]
    assert "Update: cli/agentbox update {service}" in doctor


def test_identity_list_answers_can_this_person_sign_in(monkeypatch, capsys):
    """The list shows each person's role and whether they can sign in.

    These facts are spread over four environment variables in two systemd
    units. The command gathers them, so answering "can Sam sign in?" does not
    mean looking in four places.
    """
    import agentbox_identity as cli

    monkeypatch.setattr(cli, "read_env_file",
                        lambda p: {"AGENTBOX_IDENTITIES": "alex:a,sam:b,taylor:c",
                                   "GOOGLE_BRIDGE_TOKEN_ALEX": "t"})
    monkeypatch.setattr(cli, "unit_environment", lambda unit: {
        "agentbox-portal": {"AGENTBOX_ADMINS": "alex",
                            "AGENTBOX_IDENTITY_EMAILS": "alex:f@e.com",
                            "AGENTBOX_SMTP_HOST": "smtp.example.com"},
        "agentbox-approvals": {"AGENTBOX_DISCORD_IDENTITIES": "sam:999"},
    }.get(unit, {}))

    assert cli.identity_list() == 0
    out = capsys.readouterr().out
    # Matched on whitespace rather than an exact column, which depended on the
    # longest name happening to be five characters.
    assert re.search(r"alex\s+admin", out)
    assert re.search(r"sam\s+member", out)
    # Each person's own route in, not a global claim about SMTP.
    assert "email" in out and "Discord DM" in out
    # A person with neither route is named.
    # A third, distinct name: this assertion needs somebody with no route at
    # all, and both of the others have one.
    assert "taylor cannot request a link themselves" in out


def test_identity_list_does_not_invent_a_route(monkeypatch, capsys):
    """SMTP configured but no address for that person is not a way in."""
    import agentbox_identity as cli
    monkeypatch.setattr(cli, "read_env_file",
                        lambda p: {"AGENTBOX_IDENTITIES": "sam:b"})
    monkeypatch.setattr(cli, "unit_environment", lambda unit: {
        "AGENTBOX_SMTP_HOST": "smtp.example.com"} if "portal" in unit else {})
    cli.identity_list()
    out = capsys.readouterr().out
    assert "operator link only" in out
    assert "sam cannot request a link themselves" in out


def test_setup_command_registered_in_cli():
    """`agentbox setup` must be exposed as a top-level subcommand."""
    from conftest import code_of
    source = code_of("cli/agentbox")
    assert 'sub.add_parser("setup"' in source
    assert 'args.cmd == "setup"' in source
    assert "agentbox_setup.setup(" in source


def test_setup_creates_secured_directories_and_templates(tmp_path):
    """Setup creates 0700 config dir, 0750 state dirs, templates, and sandbox launcher."""
    import importlib.machinery
    import importlib.util
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    loader = importlib.machinery.SourceFileLoader("abx_setup", str(repo / "cli" / "agentbox_setup.py"))
    spec = importlib.util.spec_from_loader("abx_setup", loader)
    setup_mod = importlib.util.module_from_spec(spec)
    sys.modules["abx_setup"] = setup_mod
    spec.loader.exec_module(setup_mod)

    test_repo = tmp_path / "repo"
    test_repo.mkdir()
    (test_repo / "cli").mkdir()
    config_dir = tmp_path / "config"
    state_dir = tmp_path / "state"

    reports = []
    code = setup_mod.setup(
        repo=test_repo,
        config_dir=config_dir,
        state_dir=state_dir,
        report=lambda lvl, msg: reports.append((lvl, msg)),
    )
    assert code == 0
    assert config_dir.is_dir()
    assert (state_dir / "backups").is_dir()
    assert (state_dir / "logs").is_dir()
    assert (config_dir / "secret-wrapper.example").is_file()
    assert (config_dir / ".env.example").is_file()
    wrapper_text = (config_dir / "secret-wrapper.example").read_text(encoding="utf-8")
    assert "infisical" in wrapper_text
    assert "bws" in wrapper_text
    assert "op run" in wrapper_text
    launcher = test_repo / "cli" / "agentbox-sandbox"
    assert launcher.is_file()
    assert "bwrap" in launcher.read_text(encoding="utf-8")


def test_deploy_supports_custom_secret_wrapper_and_providers():
    """`agentbox deploy` delegates through custom secret wrapper or native providers."""
    from conftest import code_of
    source = code_of("cli/agentbox_deploy.py")
    deploy_code = source.split("def deploy(service:")[1].split("\ndef ")[0]
    assert "AGENTBOX_SECRET_WRAPPER" in deploy_code
    assert 'Path(env_dir) / "secret-wrapper"' in deploy_code
    assert "infisical://" in deploy_code
    assert "bws://" in deploy_code
    assert "doppler://" in deploy_code

