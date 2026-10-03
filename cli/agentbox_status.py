"""What is actually running, as data rather than as printed lines.

`doctor` has known all of this for a long time, but it knows it in the form of
text on a terminal. That made the Operations page carry a card headed
"Connector health" whose entire content was a paragraph explaining why
checking health would be a good idea — the one thing an admin opening that
page wants, described rather than shown.

So the facts live here, in a shape both can use: `doctor` keeps its prose and
its carefully-argued severities, and the portal renders the same snapshot as a
page. One definition, so the two cannot drift into disagreeing about whether
the box is healthy.

## Why this reads and never writes

Everything here is `docker ps`, an HTTP GET, or a stat. Nothing restarts,
deploys, or changes a container. The portal is the most privileged web surface
on the box — it holds sessions that can edit the admin list — and giving it a
button that restarts services would make every bug in it a great deal more
expensive. Seeing that something is broken is useful on a phone; fixing it can
want a terminal.

Every subprocess is a fixed argv with no shell and no interpolated input.

## Freshness

Snapshots are cached briefly and stamped with when they were taken. A status
page that silently serves a minute-old picture of a box that just fell over is
worse than no status page, so the age is rendered rather than hidden.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

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


# What the assistant needs in order to answer at all, and what merely makes it
# better. Kept here so the page and the terminal cannot disagree about which is
# which — the severities are reasoned about at length in cli/agentbox.
ENDPOINTS: dict[str, tuple[str, str]] = {
    "main model": (
        os.environ.get("AGENTBOX_MAIN_BASE", "http://127.0.0.1:1234/v1") + "/models",
        FAIL),
    "assistant tools (MCP)": ("http://127.0.0.1:3465/health", FAIL),
    "control plane api": ("http://127.0.0.1:8000/api/evals/health", WARN),
    "control plane ui": ("http://127.0.0.1:4321/", WARN),
}

# Friendlier names for the containers a household might reasonably ask about.
# Anything not listed keeps its compose service name, which is not pretty but
# is never wrong.
SERVICE_LABELS = {
    "agentbox-mcp": "Assistant tools",
    "homeassistant": "Home Assistant",
    "homeassistant-bridge": "House control",
    "google-workspace-bridge": "Google (mail, calendar, files)",
    "memory-bridge": "Memory",
    "vikunja": "Tasks",
    "vikunja-bridge": "Tasks bridge",
    "builder-bridge": "Builder",
    "eufy-bridge": "Cameras",
}


def probe(url: str, timeout: float = 4) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def endpoint_checks(timeout: float = 4) -> list[Check]:
    """Probe every endpoint at once.

    Serially this is eight timeouts end to end, which on a box with one thing
    down is most of a minute of somebody staring at a blank page. They are
    independent, so there is no reason to wait for them in turn.
    """
    def one(item):
        name, (url, severity) = item
        return Check(name=name, ok=probe(url, timeout), detail=url,
                     severity=severity)

    with ThreadPoolExecutor(max_workers=len(ENDPOINTS) or 1) as pool:
        return list(pool.map(one, ENDPOINTS.items()))


def source_sha(service: str) -> str:
    """Hash exactly the files the Dockerfile copies, plus the compose file.

    Derived from the Dockerfile's own COPY lines rather than a directory walk,
    so it measures precisely what determines the image. Shared sources live in
    services/templates and policies/ and are copied from the repo root at build
    time — reading the COPY lines means the hash follows them without anyone
    maintaining a second list.

    This replaced a directory walk that hashed the README, so a doc edit marked
    a service stale. A freshness check that fires on documentation is one people
    learn to click past, and this one exists because a security fix once sat in
    git for three weeks without reaching the running container.

    Lives here rather than in cli/agentbox because the portal needs the same
    answer. A second implementation was written for this page and got it wrong
    — it hashed git history instead of file contents and reported every service
    on the box as stale, which is precisely the drift this module exists to
    prevent.
    """
    svc_dir = REPO / "services" / "compose" / service
    dockerfile = svc_dir / "Dockerfile"
    paths = []
    if dockerfile.exists():
        paths.append(dockerfile)
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^COPY\s+(\S+)\s", line.strip())
            if m:
                paths.append(REPO / m.group(1))
    for candidate in ("compose.yaml", "compose.yml"):
        if (svc_dir / candidate).exists():
            paths.append(svc_dir / candidate)
    h = hashlib.sha256()
    for f in sorted(set(paths), key=lambda x: str(x)):
        if not f.is_file():
            continue
        h.update(str(f.relative_to(REPO)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


@dataclass(frozen=True)
class Service:
    name: str          # compose service, e.g. homeassistant-bridge
    label: str         # what a person would call it
    container: str
    running: bool
    stale: bool        # running code older than what is committed
    status: str = ""   # docker's own words, e.g. "Up 3 hours (healthy)"
    # A compose directory nobody has deployed here is worth listing and is
    # not a fault: eufy-bridge is a household service this household chose
    # not to run, and a dashboard permanently red over it is the same
    # "teaches people to ignore the list" failure the scaffold exclusion
    # exists to prevent.
    deployed: bool = True


def opted_out() -> set[str]:
    """Services the household has said it does not run.

    Read from the settings store rather than inferred, because absence in
    `docker ps -a` cannot distinguish a service never deployed from one whose
    container was removed five minutes ago — and treating those the same
    means either `docker compose down` reads as healthy or the dashboard is
    permanently red over a deliberate choice.
    """
    raw = os.environ.get("AGENTBOX_NOT_DEPLOYED")
    if raw is None:
        try:
            path = Path(os.environ.get(
                "AGENTBOX_PORTAL_DIR",
                str(Path("~/.local/state/agentbox/portal").expanduser())))
            import json as _json
            raw = str(_json.loads(
                (path / "settings.json").read_text(encoding="utf-8")
            ).get("not_deployed", ""))
        except (OSError, ValueError):
            raw = ""
    return {n.strip() for n in (raw or "").replace(" ", ",").split(",")
            if n.strip()}


def services() -> list[Service]:
    """Agentbox-managed containers, and whether each is running current code.

    One `docker ps` rather than a pair of `docker inspect` calls per container:
    this is rendered on a page load, and twenty subprocesses would make the
    Operations page take seconds to open.
    """
    fmt = ('{{.Names}}\t{{.Label "agentbox.service"}}\t'
           '{{.Label "agentbox.source_sha"}}\t{{.Status}}')
    try:
        # `-a`, not bare `ps`. Without it the list is derived entirely from
        # what is running, so a crashed bridge does not turn red — it
        # disappears, and the page then reports "Everything is running"
        # because nothing is left to complain about. A status page whose
        # failure mode is silence is worse than none.
        out = subprocess.run(["docker", "ps", "-a", "--format", fmt],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return []

    found: list[Service] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        container, service, sha, status = (p.strip() for p in parts[:4])
        # Containers without our labels are somebody else's, and identity
        # bridges are per-person rather than per-service — neither belongs on
        # a household status page.
        if not service or service.startswith("identity-"):
            continue
        if not (REPO / "services" / "compose" / service).is_dir():
            continue
        current = source_sha(service)
        found.append(Service(
            name=service,
            label=SERVICE_LABELS.get(service, service),
            container=container,
            running=status.startswith("Up"),
            # Only claim staleness when both hashes are known. An unlabelled
            # container predates source hashing; calling that "stale" would
            # send somebody to redeploy something that is perfectly current.
            stale=bool(sha and current and sha != current),
            status=status))

    # A service that has never been deployed has no container to enumerate,
    # so it cannot appear above at all. Reported explicitly rather than left
    # out: "absent" and "healthy" must not render identically.
    # `docker ps -a` can return several containers for one service — an old
    # exited one beside the running replacement. Keep the running one, or the
    # dashboard carries a permanently red row for a container nobody uses.
    best: dict[str, Service] = {}
    for service in found:
        current = best.get(service.name)
        if current is None or (service.running and not current.running):
            best[service.name] = service
    found = list(best.values())

    seen = {service.name for service in found}
    for compose in sorted((REPO / "services" / "compose").glob("*/compose.yaml")):
        name = compose.parent.name
        # `example-service` is the scaffold's fixture, not a household
        # service; reporting it as down would be the false alarm that teaches
        # people to ignore this list.
        if name in seen or name == "example-service":
            continue
        chosen = name in opted_out()
        found.append(Service(name=name, label=SERVICE_LABELS.get(name, name),
                             container="", running=False, stale=False,
                             deployed=not chosen,
                             status=("not deployed here, by choice" if chosen
                                     else "no container — expected to be "
                                          "running")))
    return sorted(found, key=lambda s: s.label.lower())


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


@dataclass
class Snapshot:
    taken_at: int
    endpoints: list[Check] = field(default_factory=list)
    services: list[Service] = field(default_factory=list)
    other: list[Check] = field(default_factory=list)
    evaluating: bool = False

    @property
    def problems(self) -> list[Check]:
        return [c for c in self.endpoints + self.other if not c.ok]

    @property
    def failing(self) -> list[Check]:
        return [c for c in self.problems if c.severity == FAIL]

    @property
    def stale_services(self) -> list[Service]:
        return [s for s in self.services if s.stale]

    @property
    def healthy(self) -> bool:
        return not self.problems and not self.stale_services and all(
            s.running for s in self.services if s.deployed)


def docker_check() -> Check:
    """Whether container state could be read at all.

    `services()` returns [] when docker cannot be reached, and an empty list
    of services is indistinguishable from a healthy one — `all([])` is True,
    so the page said "Everything is running" precisely when it knew least.
    Not knowing has to be a finding, or silence becomes the safest-looking
    answer.
    """
    try:
        result = subprocess.run(["docker", "ps", "-q"], capture_output=True,
                                text=True, timeout=15)
        ok = result.returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("Container state", False,
                     f"docker did not answer ({type(exc).__name__}); the "
                     f"service list below is incomplete, not empty", FAIL)
    if not ok:
        return Check("Container state", False,
                     "docker did not answer; the service list below is "
                     "incomplete, not empty", FAIL)
    return Check("Container state", True, "readable")


def take() -> Snapshot:
    return Snapshot(taken_at=int(time.time()),
                    endpoints=endpoint_checks(),
                    services=services(),
                    other=[disk_check(), docker_check(), *lan_exposure()],
                    evaluating=evaluation_running())


# A page load should not pay for eight probes and a docker call every time
# somebody clicks between tabs, and two admins looking at once should not
# double the work. Short enough that a reload after fixing something shows the
# fix; the age is rendered either way so nobody has to guess.
CACHE_SECONDS = 20
_cached: Snapshot | None = None


def cached() -> Snapshot:
    global _cached
    if _cached is None or int(time.time()) - _cached.taken_at >= CACHE_SECONDS:
        _cached = take()
    return _cached
