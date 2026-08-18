"""Bring the local model stack back up when nothing is deliberately holding it down.

## What this is actually for

The stack does not crash. It is stopped, on purpose, by evaluation runs: a
compare run cannot share 16 GB of unified memory with the production model, so
`agentbox-eval` quiesces production, runs, and restores it afterwards. That is
correct and there is no way around it on this hardware — both models do not
fit.

The failure mode is narrower than "it keeps going down". It is that the
restore is the *last* thing a run does, so anything that stops a run from
finishing leaves production stopped:

  - the run is killed with SIGKILL, which no handler can intercept
  - the box loses power or reboots mid-run
  - the run dies in a way that skips its own `finally`

SIGINT, SIGTERM and SIGHUP are already handled in the evaluator's lifecycle
manager and do restore correctly — SIGHUP was added after three runs left the
stack down for hours, the last for thirteen. This covers what is left, which
is the class of failures a process cannot handle from inside itself.

## Why a watchdog rather than Restart=always

`Restart=always` does not help: systemd does not restart a unit that was
stopped deliberately, and every one of these stops is deliberate. The stop is
not the bug. Nobody putting it back is.

## Why it cannot fight an evaluation

It stands down whenever an evaluation process exists. A run holds its process
for its entire duration — quiesce happens inside the run, not before it — so
the presence of that process is a reliable "someone means this to be down".

Checked before *and* after starting anything: a run that begins while this is
mid-flight would otherwise race, and losing that race means starting a model
that steals memory from a benchmark and silently corrupts its numbers. On any
doubt this does nothing, because a late restore costs minutes and a corrupted
comparison costs a day.

## The escape hatch

`~/.local/state/agentbox/keepalive-disabled` stops it entirely. An operator
doing maintenance needs a way to keep the stack down that does not involve
racing a timer, and "delete the file" is easier to remember at 2am than a
systemctl incantation.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Ordered: models first, then the things that talk to them. Starting a router
# before the workers it routes to would have it answering for a stack that is
# not there yet — the same ordering the evaluator's own restore uses.
PRODUCTION_UNITS = (
    "agentbox-production-model.service",
    "fastcontext-worker.service",
    "vibethinker-worker.service",
    "llama-vision.service",
    "agentbox-router.service",
    "nemohermes-docker-bridge.service",
)

DISABLED_FLAG = Path(os.environ.get(
    "AGENTBOX_KEEPALIVE_FLAG",
    str(Path("~/.local/state/agentbox/keepalive-disabled").expanduser())))

# The evaluator detector lives in agentbox_status, which the portal also uses.
# One definition: a watchdog and a status page that disagree about whether a
# benchmark is running would each be right half the time.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agentbox_status  # noqa: E402


def _run(argv: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def evaluation_running() -> bool:
    """Whether an evaluation currently owns the machine.

    Delegates to agentbox_status, which reads argv from /proc rather than
    grepping command lines — see its docstring for the five processes that a
    substring match mistook for a benchmark, and the four days this watchdog
    spent standing down because of them.
    """
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
        # An answer we cannot line up with the question. Doing nothing is
        # correct: the alternative is starting units based on a guess.
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


def main(argv: list[str] | None = None) -> int:
    quiet = "--quiet" in (argv if argv is not None else sys.argv[1:])

    def say(message: str) -> None:
        if not quiet:
            print(message, file=sys.stderr)

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
    # checks above were in flight must win, because the cost is asymmetric:
    # a late restore is minutes, a model stealing memory from a benchmark is a
    # day's numbers quietly wrong.
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
