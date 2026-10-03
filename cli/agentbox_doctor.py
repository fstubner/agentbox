"""`agentbox doctor` and `agentbox status`."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from agentbox_checks import (
    assistant_containment,
    bridge_readiness,
    connector_credentials,
    portal_email_delivery,
    portal_redirect_uri,
)
from agentbox_common import FAIL, OK, REPO, WARN, probe, report
from agentbox_policy import policy_drift, source_sha
from agentbox_validate import validate

# How old a third-party image may get before doctor says so. Two weeks is
# about one Home Assistant release cycle.
IMAGE_STALE_DAYS = int(os.environ.get("AGENTBOX_IMAGE_STALE_DAYS", "14"))


# --- doctor / status --------------------------------------------------------


# Endpoints served by a user unit we can name. Knowing the unit turns "not
# responding" into an instruction.
ENDPOINT_UNITS = {"portal": "agentbox-portal"}


def third_party_images() -> list[tuple[str, str, str, int]]:
    """(service, image, local build date, age in days) for images this repo does not build.

    Every one of these is pinned to a floating tag — `stable`, `latest`, or no
    tag at all — and `deploy` runs `docker compose up -d --build`, which
    rebuilds what this repo builds and reuses whatever is already cached for
    everything else. So a floating tag never floats: it freezes at whenever it
    was first pulled and stays there.

    Found on 2026-08-14 while asking why Home Assistant was three weeks
    behind. Vikunja's image was four months old. Nothing reported either,
    because `doctor`'s staleness check compares source SHAs for images built
    here and has no notion of upstream ones.
    """
    import datetime
    out = []
    for compose in sorted((REPO / "services" / "compose").glob("*/compose.yaml")):
        service = compose.parent.name
        for line in compose.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line.startswith("image:"):
                continue
            image = line.split("image:", 1)[1].strip().strip('"\'')
            if not image or "${" in image or image.startswith("agentbox/"):
                continue
            created = subprocess.run(
                ["docker", "image", "inspect", image, "--format", "{{.Created}}"],
                capture_output=True, text=True, check=False).stdout.strip()
            if not created:
                continue
            try:
                when = datetime.datetime.fromisoformat(created.split(".")[0])
            except ValueError:
                continue
            age = (datetime.datetime.now() - when).days
            out.append((service, image, when.date().isoformat(), age))
    return out


def stopped_hint(name: str) -> str:
    """Say whether an unreachable service is *stopped*, and how to start it.

    "not responding" covers two conditions that need opposite responses: a
    process that is running and wedged, and one that is not running at all.
    The router was cleanly stopped twice in two days — signal TERM, not a
    crash, so `Restart=on-failure` never applied — and each time the only
    symptom was `triage_email` failing with "router unreachable", which reads
    like a network fault rather than a service somebody turned off.
    """
    unit = ENDPOINT_UNITS.get(name)
    if not unit:
        return ""
    try:
        state = subprocess.run(["systemctl", "--user", "is-active", unit],
                               capture_output=True, text=True, timeout=10,
                               check=False).stdout.strip()
    except Exception:  # noqa: BLE001 — a hint must never break the check
        return ""
    if state == "active":
        return "  (unit is running, so it is wedged rather than stopped)"
    return f"  ({unit} is {state or 'not running'} — "\
           f"start it: systemctl --user start {unit})"


# Every service in docs/architecture.md, so "is the platform up" is one command
# rather than a set of remembered curls.
#
# Severity is deliberate, and was corrected on 2026-08-02.
#
# The role workers were FAIL on the assumption that the router hands
# /context/extract and /reason/check to them. It does not: the live gateway
# talks straight to the main model and the MCPs, so the router and both workers
# are evaluator infrastructure, not assistant infrastructure. The evaluator's
# quiesce_commands stops both on every compare run, so FAIL meant doctor was
# red by design during every benchmark — and a check that is normally red is a
# check people stop reading.
#
# FAIL is now reserved for what the assistant actually needs to serve a
# request: the main model, and the bridge/MCP chain. Everything the assistant
# does not depend on warns.
#
# Amended 2026-10-03: the router, both workers and the vision model are
# retired, and so are the three tools that called them. They are no longer
# probed. A check for a service that is meant to be off reports it down on
# every install forever, and a check that is always red is one people stop
# reading. See router/README.md for why they were retired.
ENDPOINTS = {
    "main model": (os.environ.get("AGENTBOX_MAIN_BASE", "http://127.0.0.1:1234/v1") + "/models", FAIL),
    "agentbox-mcp": ("http://127.0.0.1:3465/health", FAIL),
    # The builder is not on the assistant's critical path — it proposes code,
    # it does not serve a request. A warning, so an outage here never masks one
    # that stops the assistant working.
    "control plane api": ("http://127.0.0.1:8000/api/evals/health", WARN),
    "control plane ui": ("http://127.0.0.1:4321/", WARN),
}


# The gateway allowlists which MCP tools the assistant may see. A tool absent
# from that list is invisible — not refused, not logged, not an error anywhere:
# the assistant simply never knows it exists. That has already happened twice,
# once leaving create_gmail_draft and find_or_create_task unreachable for days.
#
# Deliberately a warning, and deliberately quiet when unreadable. The gateway
# runs as another user and the config holds its bearer tokens, so `doctor` run
# as the operator usually cannot read it — which is correct, and not a fault to
# report as one.
GATEWAY_CONFIG = os.environ.get("AGENTBOX_GATEWAY_CONFIG",
                                os.path.expanduser("~agentbox/agentbox/config.yaml"))


def gateway_tool_visibility() -> int:
    """Check the gateway can see every tool the MCP gateway serves.

    Before consolidation this compared five per-server `include` allowlists
    against five MCPs, and it earned its keep — a tool missing from one was
    invisible to the assistant, not refused and not logged. With one endpoint
    and no allowlist the failure mode changes: the policy is the only gate, so
    what matters now is that the gateway is actually pointed at the gateway and
    that the tool count matches what the service assembles.
    """
    try:
        text = Path(GATEWAY_CONFIG).read_text(encoding="utf-8")
    except OSError:
        report(WARN, f"gateway config not readable ({GATEWAY_CONFIG}); "
                     "cannot check which tools the assistant can see")
        return 0

    port = os.environ.get("AGENTBOX_MCP_PORT", "3465")
    if f"127.0.0.1:{port}/mcp" not in text:
        report(WARN, f"gateway config does not point at the MCP gateway on "
                     f":{port}; the assistant may be using retired endpoints")
        return 0

    stale = [p for p in ("3467", "3472", "3473", "3475", "3477")
             if f"127.0.0.1:{p}/mcp" in text]
    if stale:
        report(WARN, "gateway config still references retired per-service MCPs: "
                     + ", ".join(stale))

    served = 0
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
            served = json.loads(r.read()).get("tools", 0)
    except Exception:  # noqa: BLE001
        pass
    # An `include` list is optional now, but if one exists it silently narrows
    # what the assistant sees — the exact failure this check was written for.
    includes = re.findall(r"^      - ([a-z_]+)\s*$", text, re.M)
    if includes and served and len(includes) < served:
        report(WARN, f"gateway allowlists {len(includes)} of {served} tools; "
                     f"the rest are invisible to the assistant")
        return 0
    report(OK, f"gateway points at the MCP gateway on :{port}"
               + (f"; {served} tools served" if served else ""))
    return 0


def deploy_freshness() -> int:
    """Flag running containers whose image source differs from current source."""
    stale = 0
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=10).stdout
    except Exception:
        report(WARN, "docker unavailable; skipped deploy freshness check")
        return 0
    for name in [n.strip() for n in out.splitlines() if n.strip()]:
        label = subprocess.run(
            ["docker", "inspect", name, "--format", "{{ index .Config.Labels \"agentbox.source_sha\" }}"],
            capture_output=True, text=True).stdout.strip()
        if not label or label == "<no value>":
            continue  # not an agentbox-managed service, or built before hashing existed
        service = subprocess.run(
            ["docker", "inspect", name, "--format", "{{ index .Config.Labels \"agentbox.service\" }}"],
            capture_output=True, text=True).stdout.strip().replace("<no value>", "")
        if not service or not (REPO / "services" / "compose" / service).exists():
            continue
        current = source_sha(service)
        if label != current:
            report(FAIL, f"stale: {service} running source {label} != "
                         f"current {current} — run cli/agentbox deploy {service}")
            stale += 1
        else:
            report(OK, f"deploy fresh: {service}")
    return stale


def doctor() -> int:
    bad = 0
    for tool in ("git", "curl", "docker"):
        if shutil.which(tool):
            report(OK, f"{tool} available")
        else:
            report(FAIL, f"{tool} missing")
            bad += 1
    for name, (url, severity) in ENDPOINTS.items():
        if probe(url):
            report(OK, f"{name} responding: {url}")
        else:
            report(severity, f"{name} not responding: {url}{stopped_hint(name)}")
            if severity is FAIL:
                bad += 1
    for service, image, when, age in third_party_images():
        if age > IMAGE_STALE_DAYS:
            report(WARN, f"{image} is {age} days old (pulled {when}); floating "
                         f"tags never re-pull. Update: cli/agentbox update {service}")
        else:
            report(OK, f"{image} is {age} days old")

    disk = shutil.disk_usage("/")
    pct = disk.used * 100 // disk.total
    report(OK if pct < 90 else WARN, f"disk usage {pct}%")
    bad += deploy_freshness()
    bad += policy_drift()
    bad += bridge_readiness()
    bad += assistant_containment()
    bad += connector_credentials()
    bad += portal_redirect_uri()
    bad += portal_email_delivery()
    bad += gateway_tool_visibility()
    bad += validate()
    return 1 if bad else 0


def status() -> int:
    for name, (url, _severity) in ENDPOINTS.items():
        print(f"{name:26} {'up' if probe(url) else 'DOWN':4} {url}")
    return 0
