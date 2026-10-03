"""Bring the local model stack back up unless it was stopped on purpose.

## What this is for

The stack is stopped on purpose by evaluation runs, because a benchmark model
and the production model cannot both be loaded. `agentbox-eval` stops
production, runs, and restores it afterwards.

Restoring is the last thing a run does, so anything that stops a run finishing
leaves production down. SIGINT, SIGTERM and SIGHUP are handled by the
evaluator. This covers what a process cannot handle from inside itself.

  - the run is killed with SIGKILL
  - the machine loses power or reboots mid-run
  - the run dies in a way that skips its own clean-up

## Why not Restart=always

systemd does not restart a unit that was stopped on purpose, and each of these
stops is on purpose. The stop is expected. What is missing is something that
starts the stack again afterwards.

## Why it does not interfere with an evaluation

It stands down whenever an evaluation process exists, since a run holds its
process for its whole duration. It checks before and after starting anything,
so a run that begins mid-restore is not raced. When in doubt it does nothing.
A late restore loses minutes, while a corrupted benchmark loses a day.

## Turning it off

`~/.local/state/agentbox/keepalive-disabled` stops it entirely, for
maintenance that needs the stack to stay down.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Started in order, models first and then what talks to them.
PRODUCTION_UNITS = (
    "agentbox-production-model.service",
    "nemohermes-docker-bridge.service",
)

DISABLED_FLAG = Path(os.environ.get(
    "AGENTBOX_KEEPALIVE_FLAG",
    str(Path("~/.local/state/agentbox/keepalive-disabled").expanduser())))

# The evaluator detector lives in agentbox_status, which the portal also uses.
# With one definition, the watchdog and the status page always agree on
# whether a benchmark is running.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agentbox_status  # noqa: E402


def _run(argv: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def evaluation_running() -> bool:
    """Whether an evaluation currently owns the machine. Delegates to
    agentbox_status, which reads argv from /proc rather than grepping command
    lines."""
    return agentbox_status.evaluation_running()


def inactive_units(units: tuple[str, ...] = PRODUCTION_UNITS) -> list[str]:
    """Which production units are not running.

    `is-active` exits non-zero when any unit is not active, so the exit code
    says nothing about *which*. The per-line output is the answer, and it is
    ordered to match the query.
    """
    try:
        result = _run(["systemctl", "--user", "is-active", *units], timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    states = result.stdout.split()
    if len(states) != len(units):
        # The output does not line up with the units queried. Do nothing, so
        # no unit is started based on a guess.
        return []
    return [unit for unit, state in zip(units, states, strict=True)
            if state != "active"]


def restore(units: list[str]) -> tuple[bool, str]:
    try:
        result = _run(["systemctl", "--user", "start", *units], timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()[:400]
    return True, ""


REASON_FILE = DISABLED_FLAG.with_name("keepalive-last-reason")


def main(argv: list[str] | None = None) -> int:
    quiet = "--quiet" in (argv if argv is not None else sys.argv[1:])

    def say(message: str) -> None:
        """Log a no-op the first time, then stay quiet about it.

        The unit runs every two minutes, so logging every run would bury the
        reason in noise. Logging nothing would make a watchdog that is standing
        down look the same as one that is working. It logs only when the
        reason changes.
        """
        if not quiet:
            print(message, file=sys.stderr)
            return
        try:
            previous = REASON_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            previous = ""
        if previous == message:
            return
        print(message, file=sys.stderr)
        try:
            REASON_FILE.parent.mkdir(parents=True, exist_ok=True)
            REASON_FILE.write_text(message, encoding="utf-8")
        except OSError:
            pass

    if DISABLED_FLAG.exists():
        say(f"[..] standing down: {DISABLED_FLAG} exists")
        return 0

    if evaluation_running():
        say("[..] an evaluation is running; production is meant to be down")
        return 0

    down = inactive_units()
    if not down:
        say("[ok] production stack is up")
        return 0

    # Re-check after deciding and before acting. A run that started while the
    # checks above were in flight takes priority. A late restore costs minutes.
    # A model using memory a benchmark needs makes a day's results wrong
    # without any error.
    if evaluation_running():
        say("[..] an evaluation started while checking; leaving it alone")
        return 0

    print(f"[..] restoring {len(down)} stopped unit(s): {', '.join(down)}",
          file=sys.stderr)
    ok, detail = restore(down)
    if not ok:
        print(f"[fail] could not restore production: {detail}", file=sys.stderr)
        return 1
    print("[ok] production stack restored", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
