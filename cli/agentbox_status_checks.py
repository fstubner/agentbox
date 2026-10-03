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
    # distinction is doctor's, and is argued in cli/agentbox next to ENDPOINTS.
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
        # 90 rather than 95: the thing that fills this disk is model weights
        # and backups, both of which arrive in tens of gigabytes at a time.
        ok=percent < 90,
        detail=f"{percent}% used, {free_gb:.0f} GB free",
        severity=FAIL if percent >= 95 else WARN)


def lan_exposure() -> list[Check]:
    """Local services listening on every interface rather than loopback.

    The model servers are unauthenticated: anything that can reach the port can
    use the GPU and read whatever is in a prompt. Three of the four bind to
    127.0.0.1 and one does not, which reads as an oversight rather than a
    decision — and it is invisible unless somebody thinks to run `ss`.
    """
    try:
        out = subprocess.run(["ss", "-ltn"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []

    # The portal belongs here more than anything else does: it holds sessions
    # that can edit the admin list, and it is the surface most likely to be
    # deliberately bound wide so a phone on the sofa can reach it. A check
    # written to catch an unintended 0.0.0.0 that cannot see the box's most
    # privileged service is checking the easy half of the problem.
    watched = {"1234": "main model", "1235": "context worker",
               "1236": "reason worker", "1240": "vision model",
               "8765": "router", "8000": "control plane api",
               "4321": "control plane ui", "8771": "household portal",
               "8772": "speaker"}
    # Services known to require a credential on every route. The portal is
    # here because it does: every path, including unknown ones, answers 401
    # unauthenticated. Saying otherwise made the operator's headline health
    # view state something false about the box's most privileged service —
    # and a warning that is wrong is one people learn to dismiss, which costs
    # more than the warning was worth.
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
                # Deliberately not "it asks for no password": that is unknown
                # for most of these, and asserting it was the bug.
                tail = "this check cannot confirm it requires a credential"
            exposed.append(Check(
                name=f"{watched[port]} reachable from the network",
                ok=False,
                detail=f"listening on {local} rather than 127.0.0.1 — anything "
                       f"on the LAN can reach it, and {tail}",
                severity=WARN))
    return exposed


EVALUATOR_NAMES = ("agentbox-eval", "agentbox_evals")


def looks_like_evaluator(argv: list[str]) -> bool:
    """Whether this argv *is* the evaluator, rather than merely naming it.

    Only argv[0] and argv[1] count — the program, and the script a launcher
    like `python .../agentbox-eval` runs. Anything further along is an
    argument, and an argument that happens to say `agentbox-eval` is exactly
    what fooled the previous implementation.
    """
    if not argv:
        return False
    if os.path.basename(argv[0]) in EVALUATOR_NAMES:
        return True          # the console script, executed directly
    # argv[1] counts only when argv[0] is an interpreter running it. Without
    # that guard `vim /path/to/agentbox-eval` reads as a benchmark — the same
    # names-it-versus-is-it confusion, one level down.
    if not os.path.basename(argv[0]).startswith("python"):
        return False
    if len(argv) > 1 and os.path.basename(argv[1]) in EVALUATOR_NAMES:
        return True
    # `python -m agentbox_evals` puts the module at argv[2]. The previous
    # substring matcher caught this shape and the first version of this
    # function did not — a narrowing in the direction the design explicitly
    # refuses, since a false negative costs a benchmark.
    return len(argv) > 2 and argv[1] == "-m" and argv[2] in EVALUATOR_NAMES


def evaluation_running() -> bool:
    """Whether the evaluator itself is running.

    Reads argv[0]/argv[1] from /proc rather than grepping command lines,
    because a command line that *mentions* the evaluator is not one. On this
    box `pgrep -f agentbox-eval` matched five processes and none of them was
    a run: a `systemd-inhibit --why=agentbox-eval ... sleep 604800` holding a
    seven-day wakelock, its sudo parent, a thermal sampler under an
    `agentbox-evals/` path, a launcher shell carrying the binary path in a
    nohup string, and — inevitably — the diagnostic command doing the
    grepping. A real run *is* the binary; everything else merely names it.

    Reported as a fact about the evaluator, never as a claim about the
    stack. `certify` runs without quiescing production, so "an evaluation is
    running" and "the assistant is down" are independent — conflating them
    is what made the Operations page announce a paused evaluation above six
    healthy services for four days.
    """
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        # Cannot enumerate at all: that is doubt, and doubt means a benchmark
        # might own the machine. A late restore costs minutes; starting a
        # model into a running comparison costs a day of numbers.
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
