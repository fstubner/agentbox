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


# Endpoints served by a user unit we can name. Knowing the unit lets the
# report say how to start it, instead of only "not responding".
ENDPOINT_UNITS = {"portal": "agentbox-portal"}


def third_party_images() -> list[tuple[str, str, str, int]]:
    """Service, image, build date and age in days for images built elsewhere.

    These use floating tags such as `stable` or `latest`, but `deploy` reuses
    whatever is cached, so a floating tag stays at whatever was first pulled.
    The reported age tells the operator when to run `agentbox update`.
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
    """Say whether an unreachable service is stopped, and how to start it.

    "Not responding" can mean running but stuck, or not running at all, and
    the two need opposite fixes. A cleanly stopped unit is not restarted by
    `Restart=on-failure`, so it can look like a network fault.
    """
    unit = ENDPOINT_UNITS.get(name)
    if not unit:
        return ""
    try:
        state = subprocess.run(["systemctl", "--user", "is-active", unit],
                               capture_output=True, text=True, timeout=10,
                               check=False).stdout.strip()
    except Exception:  # noqa: BLE001 (a hint must never break the check)
        return ""
    if state == "active":
        return "  (unit is running, so it is wedged rather than stopped)"
    return f"  ({unit} is {state or 'not running'}, "\
           f"start it: systemctl --user start {unit})"


# The endpoints `doctor` and `status` probe. FAIL is for what the assistant
# needs to answer a request, which is the main model and agentbox-mcp.
# Everything else warns. A check that fails during normal operation gets
# ignored, so only those two fail.
ENDPOINTS = {
    "main model": (os.environ.get("AGENTBOX_MAIN_BASE", "http://127.0.0.1:1234/v1") + "/models", FAIL),
    "agentbox-mcp": ("http://127.0.0.1:3465/health", FAIL),
    # The control plane is not on the assistant's request path, so it warns.
    "control plane api": ("http://127.0.0.1:8000/api/evals/health", WARN),
    "control plane ui": ("http://127.0.0.1:4321/", WARN),
}


# The gateway config can list which tools the assistant sees, and a tool left
# off that list is invisible, with no error anywhere. This warns about it. The
# config belongs to the gateway user and holds its tokens, so the operator
# usually cannot read it. An unreadable config is a warning, not a fault.
GATEWAY_CONFIG = os.environ.get("AGENTBOX_GATEWAY_CONFIG",
                                os.path.expanduser("~agentbox/agentbox/config.yaml"))


def gateway_tool_visibility() -> int:
    """Check the gateway points at agentbox-mcp and can see every tool it serves."""
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
    # An `include` list is optional, but if present it hides every tool not
    # on it.
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
                         f"current {current}, run cli/agentbox deploy {service}")
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
