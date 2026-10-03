"""One check's result, and the host-level checks: disk, anything listening
beyond the LAN, and whether an evaluation has the stack stopped."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

FAIL = "fail"
WARN = "warn"
OK = "ok"


@dataclass(frozen=True)
class Check:
    """One fact about the box, with how much it matters if it is false."""

    name: str
    ok: bool
    detail: str
    # Only consulted when ok is False. FAIL means the assistant cannot serve a
    # request; WARN means something is degraded but answers still happen. The
    # distinction is doctor's, and is explained in cli/agentbox_doctor.py next
    # to ENDPOINTS.
    severity: str = WARN

    @property
    def level(self) -> str:
        return OK if self.ok else self.severity


def disk_check(path: str = "/") -> Check:
    usage = shutil.disk_usage(path)
    percent = round(usage.used / usage.total * 100)
    free_gb = usage.free / 1_000_000_000
    return Check(
        name="Disk",
        # The limit is 90, not 95, because model weights and backups fill this
        # disk, and both arrive tens of gigabytes at a time.
        ok=percent < 90,
        detail=f"{percent}% used, {free_gb:.0f} GB free",
        severity=FAIL if percent >= 95 else WARN)


def lan_exposure() -> list[Check]:
    """Local services listening on every interface rather than loopback.

    The model servers have no authentication, so anything that can reach the
    port can use the GPU and read prompts. This makes a stray binding visible
    without anyone having to run `ss`.
    """
    try:
        out = subprocess.run(["ss", "-ltn"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []

    # The portal must be watched. It holds sessions that can edit the admin
    # list, and it is the service most likely to be bound to every interface
    # so a phone can reach it. It is the box's most privileged service, so a
    # check for an unintended 0.0.0.0 has to cover it.
    watched = {"1234": "main model", "1235": "context worker",
               "1236": "reason worker", "1240": "vision model",
               "8765": "router", "8000": "control plane api",
               "4321": "control plane ui", "8771": "household portal",
               "8772": "speaker"}
    # Services that require a credential on every route. The portal answers
    # 401 on every path, including unknown ones, so warning that it is exposed
    # without one would be false. False warnings lead people to ignore real
    # ones.
    authenticated = {"8771": "it authenticates every route"}

    exposed = []
    for line in out.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 4:
            continue
        local = fields[3]
        address, _, port = local.rpartition(":")
        if port in watched and address in ("0.0.0.0", "*", "[::]", "::"):
            if port in authenticated:
                tail = (f"{authenticated[port]}, so this is a wider surface "
                        f"rather than open data")
            else:
                # Do not claim "it asks for no password". That is unknown
                # for most of these services.
                tail = "this check cannot confirm it requires a credential"
            exposed.append(Check(
                name=f"{watched[port]} reachable from the network",
                ok=False,
                detail=f"listening on {local} rather than 127.0.0.1, so anything "
                       f"on the LAN can reach it, and {tail}",
                severity=WARN))
    return exposed


EVALUATOR_NAMES = ("agentbox-eval", "agentbox_evals")


def looks_like_evaluator(argv: list[str]) -> bool:
    """Whether this argv is the evaluator, rather than merely naming it.

    Only argv[0] and argv[1] count, the program and the script a launcher
    such as `python .../agentbox-eval` runs. Anything later is an argument,
    which may mention the evaluator without being it.
    """
    if not argv:
        return False
    if os.path.basename(argv[0]) in EVALUATOR_NAMES:
        return True          # the console script, executed directly
    # argv[1] counts only when argv[0] is an interpreter running it, so
    # `vim /path/to/agentbox-eval` is not a benchmark.
    if not os.path.basename(argv[0]).startswith("python"):
        return False
    if len(argv) > 1 and os.path.basename(argv[1]) in EVALUATOR_NAMES:
        return True
    # `python -m agentbox_evals` puts the module at argv[2]. Missing a real
    # run would corrupt a benchmark, so this case must be caught.
    return len(argv) > 2 and argv[1] == "-m" and argv[2] in EVALUATOR_NAMES


def evaluation_running() -> bool:
    """Whether the evaluator itself is running.

    Reads argv from /proc rather than grepping command lines, because a
    command line that mentions the evaluator is not necessarily a run. A
    wakelock, a sampler, a launcher shell and the grep itself can all name it.

    Reported as a fact about the evaluator, never about the stack. A
    certification run does not stop production, so "an evaluation is running"
    and "the assistant is down" are separate facts.
    """
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        # If processes cannot be listed, assume a benchmark might own the
        # machine. A late restore costs minutes. Starting a model during a
        # running comparison costs a day of results.
        return True
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                argv = [a.decode("utf-8", "replace")
                        for a in handle.read().split(b"\0") if a]
        except OSError:
            continue          # the process exited, or is not ours to read
        if looks_like_evaluator(argv):
            return True
    return False
