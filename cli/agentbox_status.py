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
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from agentbox_status_checks import (  # noqa: F401
    EVALUATOR_NAMES,
    FAIL,
    OK,
    WARN,
    Check,
    disk_check,
    evaluation_running,
    lan_exposure,
    looks_like_evaluator,
)

REPO = Path(__file__).resolve().parent.parent


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
