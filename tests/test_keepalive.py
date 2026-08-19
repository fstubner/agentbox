"""The watchdog that puts the model stack back.

Its one dangerous mistake is starting a model while a benchmark is running:
that steals unified memory from the run and makes its numbers quietly wrong,
which is worse than the outage it exists to fix. So most of this file is about
when it must do nothing.
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
    """The stop is deliberate. Undoing it mid-run corrupts the benchmark."""
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
    assert fake.calls == []      # it does not even look


def test_it_does_nothing_when_everything_is_already_up(ka, monkeypatch):
    fake = Fake(pgrep=(1, ""),
                **{"is-active": (0, "active\n" * len(ka.PRODUCTION_UNITS))})
    monkeypatch.setattr(ka, "evaluation_running", lambda: False)
    monkeypatch.setattr(ka, "_run", fake)
    assert ka.main(["--quiet"]) == 0
    assert fake.started() == []


def test_an_unreadable_unit_list_starts_nothing(ka, monkeypatch):
    """Output that does not line up with the question is not an answer.

    Starting units on a guess is exactly the failure this must not have.
    """
    fake = Fake(pgrep=(1, ""), **{"is-active": (0, "active\n")})   # 1 line, 6 units
    monkeypatch.setattr(ka, "evaluation_running", lambda: False)
    monkeypatch.setattr(ka, "_run", fake)
    assert ka.inactive_units() == []
    assert ka.main(["--quiet"]) == 0
    assert fake.started() == []


def test_a_run_starting_mid_check_wins(ka, monkeypatch):
    """The race guard.

    First pgrep says nothing is running, units look down, and by the time it
    is about to act a run has begun. It must notice rather than push a model
    into a benchmark's memory.
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
    """The whole point: SIGKILL and power loss skip the evaluator's restore."""
    fake = Fake(pgrep=(1, ""),
                **{"is-active": (3, "inactive\n" * len(ka.PRODUCTION_UNITS))})
    monkeypatch.setattr(ka, "evaluation_running", lambda: False)
    monkeypatch.setattr(ka, "_run", fake)
    assert ka.main(["--quiet"]) == 0
    started = fake.started()
    assert len(started) == 1
    assert set(started[0][3:]) == set(ka.PRODUCTION_UNITS)


def test_it_starts_only_what_is_actually_down(ka, monkeypatch):
    states = ["active", "inactive", "active", "active", "inactive", "active"]
    fake = Fake(pgrep=(1, ""),
                **{"is-active": (3, "\n".join(states) + "\n")})
    monkeypatch.setattr(ka, "evaluation_running", lambda: False)
    monkeypatch.setattr(ka, "_run", fake)
    assert ka.main(["--quiet"]) == 0
    assert fake.started()[0][3:] == ["fastcontext-worker.service",
                                     "agentbox-router.service"]


def test_models_are_started_before_the_router_that_needs_them(ka):
    """A router answering for workers that are not up yet is a worse state
    than one that is plainly down."""
    units = list(ka.PRODUCTION_UNITS)
    assert units.index("agentbox-production-model.service") < units.index(
        "agentbox-router.service")
    assert units.index("vibethinker-worker.service") < units.index(
        "agentbox-router.service")


def test_a_failed_restore_is_reported_not_swallowed(ka, monkeypatch):
    fake = Fake(pgrep=(1, ""),
                **{"is-active": (3, "inactive\n" * len(ka.PRODUCTION_UNITS)),
                   "start": (1, "Failed to start")})
    monkeypatch.setattr(ka, "evaluation_running", lambda: False)
    monkeypatch.setattr(ka, "_run", fake)
    assert ka.main(["--quiet"]) == 1


def test_restart_always_would_not_have_worked(ka):
    """Recorded because it is the obvious fix and it is wrong.

    systemd does not restart a unit that was stopped deliberately, and every
    one of these stops is a deliberate `systemctl stop` from the evaluator.
    """
    # code_of, not read_text: the unit's own comment explains why
    # Restart=always is wrong, and matching that comment is how this exact
    # assertion has failed in this repo four times now.
    source = code_of("cli/agentbox-keepalive.service")
    assert "Restart=always" not in source
    assert "Type=oneshot" in source


# --- what actually counts as an evaluation ------------------------------------


def test_naming_the_evaluator_is_not_being_it(ka):
    """The bug an independent acceptance pass found.

    `pgrep -f agentbox-eval` matched five processes on the live box and none
    was a run: a seven-day `systemd-inhibit --why=agentbox-eval ... sleep`
    wakelock, its sudo parent, a thermal sampler under an `agentbox-evals/`
    path, a launcher shell carrying the binary path in a nohup string, and the
    diagnostic command doing the grepping. The watchdog stood down for four
    days with the stack fully up.
    """
    status = sys.modules["agentbox_status"]
    wrappers = [
        "sudo -n systemd-inhibit --what=sleep:idle --why=agentbox-eval "
        "rescreen post-clean sleep 604800".split(),
        "bash /home/alex/.local/state/agentbox-evals/thermal-sampler.sh".split(),
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
        # No escape clause. The first version of this test read
        # `... or argv[1] == "-m"`, which made the module form pass without
        # being detected at all — a test that excused its own failure and
        # implied coverage that did not exist.
        assert status.looks_like_evaluator(argv), argv


def test_editing_the_evaluator_is_not_running_it(ka):
    """argv[1] only counts when argv[0] is the interpreter running it."""
    status = sys.modules["agentbox_status"]
    assert not status.looks_like_evaluator(["vim", "/opt/evals/agentbox-eval"])
    assert not status.looks_like_evaluator(["less", "/opt/evals/agentbox-eval"])


def test_an_unreadable_process_table_still_counts_as_running(ka, monkeypatch):
    """Restored. The first fix inverted this and deleted the test that said so,
    while the module docstring still promised doubt means do nothing."""
    status = sys.modules["agentbox_status"]

    def explode(path):
        raise OSError("cannot enumerate /proc")

    monkeypatch.setattr(status.os, "listdir", explode)
    assert status.evaluation_running() is True


def test_there_is_one_definition_of_a_running_evaluation(ka):
    """A watchdog and a status page that disagree would each be right half
    the time."""
    source = code_of("cli/agentbox_keepalive.py")
    assert "agentbox_status.evaluation_running()" in source
    assert "pgrep" not in source
