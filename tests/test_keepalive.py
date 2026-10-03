"""The watchdog that restarts the model stack.

The dangerous mistake is starting a model while a benchmark is running. That
takes unified memory from the run and makes its numbers wrong without any
error, which is worse than the outage the watchdog fixes. So most of this file
is about when it must do nothing.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import code_of

REPO = Path(__file__).resolve().parent.parent


def load_keepalive(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBOX_KEEPALIVE_FLAG", str(tmp_path / "disabled"))
    spec = importlib.util.spec_from_file_location(
        "agentbox_keepalive", REPO / "cli" / "agentbox_keepalive.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["agentbox_keepalive"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ka(tmp_path, monkeypatch):
    return load_keepalive(tmp_path, monkeypatch)


class Fake:
    """Stands in for subprocess.run, recording what was asked."""

    def __init__(self, **returns):
        self.returns = returns
        self.calls: list[list[str]] = []

    def __call__(self, argv, timeout=None):
        self.calls.append(list(argv))
        key = argv[0] if argv[0] != "systemctl" else argv[2]
        code, out = self.returns.get(key, (0, ""))
        if callable(code):
            code, out = code(len(self.calls))
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr="")

    def started(self) -> list[list[str]]:
        return [c for c in self.calls if c[:3] == ["systemctl", "--user", "start"]]


# --- when it must do nothing ---------------------------------------------------


def test_it_stands_down_while_an_evaluation_runs(ka, monkeypatch):
    """The evaluator stops the stack on purpose. Undoing that mid-run
    corrupts the benchmark."""
    fake = Fake()
    monkeypatch.setattr(ka, "evaluation_running", lambda: True)
    monkeypatch.setattr(ka, "_run", fake)
    assert ka.main(["--quiet"]) == 0
    assert fake.started() == []


def test_the_disable_flag_stops_it_entirely(ka, tmp_path, monkeypatch):
    (tmp_path / "disabled").write_text("maintenance")
    fake = Fake()
    monkeypatch.setattr(ka, "_run", fake)
    assert ka.main(["--quiet"]) == 0
    assert fake.calls == []      # it does not check any state


def test_it_does_nothing_when_everything_is_already_up(ka, monkeypatch):
    fake = Fake(pgrep=(1, ""),
                **{"is-active": (0, "active\n" * len(ka.PRODUCTION_UNITS))})
    monkeypatch.setattr(ka, "evaluation_running", lambda: False)
    monkeypatch.setattr(ka, "_run", fake)
    assert ka.main(["--quiet"]) == 0
    assert fake.started() == []


def test_an_unreadable_unit_list_starts_nothing(ka, monkeypatch):
    """Output with the wrong number of lines is treated as unknown.

    Starting units on a guess is the failure this must avoid.
    """
    fake = Fake(pgrep=(1, ""), **{"is-active": (0, "active\n")})   # 1 line, 6 units
    monkeypatch.setattr(ka, "evaluation_running", lambda: False)
    monkeypatch.setattr(ka, "_run", fake)
    assert ka.inactive_units() == []
    assert ka.main(["--quiet"]) == 0
    assert fake.started() == []


def test_a_run_starting_mid_check_wins(ka, monkeypatch):
    """The race guard.

    First pgrep says nothing is running and units look down, but a run starts
    before the watchdog acts. It must check again and not start a model in a
    benchmark's memory.
    """
    calls = []

    def racing():
        calls.append(1)
        return len(calls) > 1      # clear first, busy on the re-check

    fake = Fake(**{"is-active": (3, "inactive\n" * len(ka.PRODUCTION_UNITS))})
    monkeypatch.setattr(ka, "evaluation_running", racing)
    monkeypatch.setattr(ka, "_run", fake)
    assert ka.main(["--quiet"]) == 0
    assert fake.started() == []


# --- when it must act ----------------------------------------------------------


def test_it_restores_units_an_abandoned_run_left_down(ka, monkeypatch):
    """SIGKILL and power loss skip the evaluator's own restore step."""
    fake = Fake(pgrep=(1, ""),
                **{"is-active": (3, "inactive\n" * len(ka.PRODUCTION_UNITS))})
    monkeypatch.setattr(ka, "evaluation_running", lambda: False)
    monkeypatch.setattr(ka, "_run", fake)
    assert ka.main(["--quiet"]) == 0
    started = fake.started()
    assert len(started) == 1
    assert set(started[0][3:]) == set(ka.PRODUCTION_UNITS)


def test_it_starts_only_what_is_actually_down(ka, monkeypatch):
    states = ["active", "inactive"]
    fake = Fake(pgrep=(1, ""),
                **{"is-active": (3, "\n".join(states) + "\n")})
    monkeypatch.setattr(ka, "evaluation_running", lambda: False)
    monkeypatch.setattr(ka, "_run", fake)
    assert ka.main(["--quiet"]) == 0
    assert fake.started()[0][3:] == ["nemohermes-docker-bridge.service"]


def test_models_are_started_before_the_router_that_needs_them(ka):
    """A bridge that answers for a model that is not up yet is worse than a
    bridge that is down."""
    units = list(ka.PRODUCTION_UNITS)
    assert units.index("agentbox-production-model.service") < units.index(
        "nemohermes-docker-bridge.service")


def test_a_failed_restore_is_reported_not_swallowed(ka, monkeypatch):
    fake = Fake(pgrep=(1, ""),
                **{"is-active": (3, "inactive\n" * len(ka.PRODUCTION_UNITS)),
                   "start": (1, "Failed to start")})
    monkeypatch.setattr(ka, "evaluation_running", lambda: False)
    monkeypatch.setattr(ka, "_run", fake)
    assert ka.main(["--quiet"]) == 1


def test_restart_always_would_not_have_worked(ka):
    """Restart=always looks like the fix, but it does not work here.

    systemd does not restart a unit stopped with `systemctl stop`, and every
    one of these stops is a `systemctl stop` from the evaluator.
    """
    # code_of, not read_text. The unit's own comment explains why
    # Restart=always is wrong, and this assertion must not match that comment.
    source = code_of("cli/agentbox-keepalive.service")
    assert "Restart=always" not in source
    assert "Type=oneshot" in source


# --- what counts as an evaluation ---------------------------------------------


def test_naming_the_evaluator_is_not_being_it(ka):
    """A process that mentions the evaluator is not an evaluation run.

    `pgrep -f agentbox-eval` matches processes that are not runs. Examples are
    a `systemd-inhibit --why=agentbox-eval ... sleep` wakelock and its sudo
    parent, a thermal sampler under an `agentbox-evals/` path, a launcher
    shell carrying the binary path in a nohup string, and the diagnostic
    command doing the grepping. Matching them would make the watchdog stand
    down while the stack is up.
    """
    status = sys.modules["agentbox_status"]
    wrappers = [
        "sudo -n systemd-inhibit --what=sleep:idle --why=agentbox-eval "
        "rescreen post-clean sleep 604800".split(),
        "bash /home/operator/.local/state/agentbox-evals/thermal-sampler.sh".split(),
        ["/bin/bash", "-c", "nohup .venv/bin/agentbox-eval certify &"],
        ["python3", "-c", "import agentbox_evals"],
        ["pgrep", "-af", "agentbox-eval"],
    ]
    for argv in wrappers:
        assert not status.looks_like_evaluator(argv), argv


def test_the_evaluator_itself_still_counts(ka):
    status = sys.modules["agentbox_status"]
    for argv in (
        ["/opt/evals/.venv/bin/python", "/opt/evals/.venv/bin/agentbox-eval",
         "run", "--stage", "compare"],
        ["/opt/evals/.venv/bin/agentbox-eval", "certify"],
        ["python", "-m", "agentbox_evals"],
    ):
        # No escape clause, so the module form must be detected.
        assert status.looks_like_evaluator(argv), argv


def test_editing_the_evaluator_is_not_running_it(ka):
    """argv[1] only counts when argv[0] is the interpreter running it."""
    status = sys.modules["agentbox_status"]
    assert not status.looks_like_evaluator(["vim", "/opt/evals/agentbox-eval"])
    assert not status.looks_like_evaluator(["less", "/opt/evals/agentbox-eval"])


def test_an_unreadable_process_table_still_counts_as_running(ka, monkeypatch):
    """When in doubt, the watchdog does nothing, as the module docstring
    states."""
    status = sys.modules["agentbox_status"]

    def explode(path):
        raise OSError("cannot enumerate /proc")

    monkeypatch.setattr(status.os, "listdir", explode)
    assert status.evaluation_running() is True


def test_there_is_one_definition_of_a_running_evaluation(ka):
    """The watchdog and the status page use the same check, so they cannot
    disagree."""
    source = code_of("cli/agentbox_keepalive.py")
    assert "agentbox_status.evaluation_running()" in source
    assert "pgrep" not in source
