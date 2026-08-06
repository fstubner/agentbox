"""Tests for the builder sandbox (architecture extension point #4).

The sandbox generates a new bridge and stops. The properties that matter are
about what it refuses and what it guarantees, not what it writes:

- it never deploys and never merges — merge_own_pr is always_denied, and a
  generated service that deployed itself would route straight around that;
- the output is correct-by-construction, so review is about whether the service
  should exist rather than whether it forgot a guardrail;
- it refuses rather than overwrites, and refuses rather than colliding.

These run the scaffolder against a throwaway copy of the repo so no test can
leave a service behind in the real one.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "cli" / "agentbox"


@pytest.fixture
def sandbox(tmp_path):
    """A minimal but complete copy of the repo the scaffolder needs."""
    root = tmp_path / "repo"
    (root / "services" / "compose").mkdir(parents=True)
    (root / "tests").mkdir()
    shutil.copytree(REPO / "services" / "templates", root / "services" / "templates")
    shutil.copytree(REPO / "policies", root / "policies")
    (root / "cli").mkdir()
    shutil.copy2(CLI, root / "cli" / "agentbox")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    return root


def run_scaffold(root, *args):
    env = dict(os.environ, AGENTBOX_REPO=str(root))
    return subprocess.run([sys.executable, str(root / "cli" / "agentbox"), "scaffold", *args],
                          cwd=root, capture_output=True, text=True, env=env)


def test_scaffold_creates_a_complete_service(sandbox):
    result = run_scaffold(sandbox, "todoist")
    assert result.returncode == 0, result.stdout + result.stderr
    service = sandbox / "services" / "compose" / "todoist-bridge"
    for expected in ("compose.yaml", "Dockerfile", "app/bridge.py",
                     "todoist-bridge.op.env.example", "README.md"):
        assert (service / expected).exists(), f"missing {expected}"


def test_shared_base_is_not_vendored(sandbox):
    """It is copied from the repo root at build time, so there is no second
    copy that could drift."""
    run_scaffold(sandbox, "todoist")
    assert not (sandbox / "services/compose/todoist-bridge/app/bridge_base.py").exists()
    dockerfile = (sandbox / "services/compose/todoist-bridge/Dockerfile").read_text()
    assert "services/templates/bridge/app/bridge_base.py" in dockerfile


def test_generated_build_context_is_the_repo_root(sandbox):
    run_scaffold(sandbox, "todoist")
    compose = (sandbox / "services/compose/todoist-bridge/compose.yaml").read_text()
    assert "context: ../../.." in compose
    assert "dockerfile: services/compose/todoist-bridge/Dockerfile" in compose


def test_generated_compose_keeps_the_platform_guardrails(sandbox):
    run_scaffold(sandbox, "todoist")
    text = (sandbox / "services/compose/todoist-bridge/compose.yaml").read_text()
    assert "127.0.0.1" in text
    assert "no-new-privileges:true" in text
    assert "mem_limit" in text
    assert "0.0.0.0:" not in text


def test_credentials_are_two_distinct_env_vars(sandbox):
    """The upstream secret and the token guarding the door must never be one."""
    run_scaffold(sandbox, "todoist")
    env = (sandbox / "services/compose/todoist-bridge/todoist-bridge.op.env.example").read_text()
    assert "TODOIST_API_TOKEN=op://" in env
    assert "TODOIST_BRIDGE_TOKEN=op://" in env


def test_no_raw_secret_lands_in_the_generated_env_example(sandbox):
    run_scaffold(sandbox, "todoist")
    env = (sandbox / "services/compose/todoist-bridge/todoist-bridge.op.env.example").read_text()
    for line in env.splitlines():
        if "TOKEN=" in line:
            assert line.split("=", 1)[1].startswith("op://")


def test_it_commits_to_a_branch_and_does_not_touch_main(sandbox):
    run_scaffold(sandbox, "todoist")
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                            cwd=sandbox, capture_output=True, text=True).stdout.strip()
    assert branch == "scaffold/todoist-bridge"
    files_on_main = subprocess.run(["git", "ls-tree", "-r", "--name-only", "master"],
                                   cwd=sandbox, capture_output=True, text=True).stdout
    if not files_on_main:
        files_on_main = subprocess.run(["git", "ls-tree", "-r", "--name-only", "main"],
                                       cwd=sandbox, capture_output=True, text=True).stdout
    assert "todoist-bridge" not in files_on_main


def test_existing_service_is_refused_not_overwritten(sandbox):
    assert run_scaffold(sandbox, "todoist").returncode == 0
    marker = sandbox / "services/compose/todoist-bridge/app/bridge.py"
    marker.write_text("# hand-written work that must not be destroyed\n")
    second = run_scaffold(sandbox, "todoist")
    assert second.returncode == 1
    assert "already exists" in second.stdout
    assert marker.read_text().startswith("# hand-written work")


def test_port_collision_is_refused(sandbox):
    run_scaffold(sandbox, "todoist")
    taken = run_scaffold(sandbox, "linear", "--port", "3480")
    used = (sandbox / "services/compose/todoist-bridge/compose.yaml").read_text()
    if ":3480:8080" in used:
        assert taken.returncode == 1
        assert "already mapped" in taken.stdout


def test_second_service_gets_a_different_port(sandbox):
    run_scaffold(sandbox, "todoist")
    run_scaffold(sandbox, "linear")
    first = (sandbox / "services/compose/todoist-bridge/compose.yaml").read_text()
    second = (sandbox / "services/compose/linear-bridge/compose.yaml").read_text()
    import re

    def port_of(text):
        return re.search(r":(\d{4}):8080", text).group(1)

    assert port_of(first) != port_of(second)


@pytest.mark.parametrize("bad", ["Todoist", "todo_ist", "9lives", "a" * 40, "todo ist"])
def test_invalid_names_are_refused(sandbox, bad):
    assert run_scaffold(sandbox, bad).returncode == 1


def test_nothing_is_deployed(sandbox):
    """The sandbox writes files. Deploying is a separate, human decision."""
    result = run_scaffold(sandbox, "todoist")
    assert "docker" not in result.stdout.lower() or "not deployed" in result.stdout
    assert "not merged, not deployed" in result.stdout
