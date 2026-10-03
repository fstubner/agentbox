"""The live-host checks `doctor` runs beyond endpoint probes."""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from agentbox_common import FAIL, GATEWAY_HOME, GATEWAY_USER, OK, WARN, agent_can, env_dir, report

# Bridges publish no host ports, so their readiness is read through
# agentbox-mcp's /ready, which probes each bridge over the container networks
# and names any that are not ready. A bridge's /ready also probes the service
# behind it, so a stopped Vikunja shows up here even while its bridge is
# healthy.
BRIDGE_READY_PORTS = {
    "agentbox-mcp": 3465,
}


# Longer than the bridge's own upstream timeout (5 seconds), so a hanging
# upstream is reported as an outage rather than as "not deployed".
READY_TIMEOUT = 10


def portal_email_delivery() -> int:
    """Warn when the portal can make sign-in links but cannot deliver them.

    The sign-in page answers the same way for known and unknown addresses, so
    it cannot say delivery failed. Someone is told a link is on its way and
    nothing arrives. This check is the only place that problem is reported.
    """
    unit_env = {}
    result = subprocess.run(
        ["systemctl", "--user", "show", "agentbox-portal", "-p", "Environment"],
        capture_output=True, text=True, timeout=20, check=False)
    for chunk in (result.stdout or "").replace("Environment=", "").split():
        key, _, value = chunk.partition("=")
        unit_env[key] = value
    if not unit_env.get("AGENTBOX_IDENTITY_EMAILS"):
        return 0
    # Discord DMs count as delivery too. They are safe for the same reason
    # email is, because the link only works in the browser that asked for it.
    channels = []
    if unit_env.get("AGENTBOX_SMTP_HOST"):
        channels.append("email")
    if unit_env.get("AGENTBOX_DISCORD_IDENTITIES"):
        channels.append("Discord DM")
    if channels:
        report(OK, f"portal can deliver sign-in links by "
                   f"{' and '.join(channels)}")
        return 0
    report(WARN, "portal has addresses configured but no delivery channel, so "
                 "sign-in links are minted and reach nobody, and the page "
                 "cannot say so because answering differently for a registered "
                 "address would reveal who lives here. Set AGENTBOX_SMTP_HOST "
                 "or AGENTBOX_DISCORD_IDENTITIES, or hand links over with: "
                 "cli/agentbox-portal link <name>")
    return 0


def portal_redirect_uri() -> int:
    """Fail early if the portal's OAuth redirect is one Google will refuse,
    such as a `.local` name, before anyone tries to register it."""
    url = os.environ.get("AGENTBOX_PORTAL_URL", "")
    if not url:
        return 0
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in ("127.0.0.1", "::1", "localhost"):
        if parsed.scheme != "http":
            report(FAIL, "loopback portal URLs must use http for Google OAuth")
            return 1
        return 0
    if parsed.scheme != "https" or host.endswith(".local") or "." not in host:
        report(FAIL, f"AGENTBOX_PORTAL_URL={url} cannot be registered as a "
                     f"Google redirect URI. Google accepts loopback over http, "
                     f"or a real public domain over https. The console refuses .local "
                     f"and bare hostnames.")
        return 1
    return 0


def connector_credentials() -> int:
    """Check each person's Google credential still works.

    A token revoked at Google leaves the container healthy. Otherwise the
    problem only shows as a 403 on a later request, possibly days later. A
    disconnected account is a legitimate state, so this warns rather than
    fails.
    """
    directory = Path(env_dir())
    for env_path in sorted(directory.glob("*-google-bridge.env")):
        identity = env_path.name[:-len("-google-bridge.env")]
        container = f"{identity}-google-bridge"
        result = subprocess.run(
            ["docker", "exec", container, "python", "-c",
             "import json,urllib.request;"
             "print(urllib.request.urlopen("
             "'http://127.0.0.1:8080/ready', timeout=10).read().decode())"],
            capture_output=True, text=True, timeout=60, check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:120]
            report(WARN, f"{identity}'s google bridge did not answer: {detail}")
            continue
        if '"ok": true' in result.stdout.lower() or '"ok":true' in result.stdout.lower():
            report(OK, f"{identity}'s google credential is live")
        else:
            report(WARN, f"{identity}'s google credential looks dead, so they "
                         f"can reconnect at the portal, then run: "
                         f"cli/agentbox identity reconnect {identity}")
    # Always zero, because a disconnected account is not a broken deployment.
    return 0


def assistant_containment() -> int:
    """Verify the assistant still cannot rewrite what constrains it.

    This rests on file ownership, which a careless `chown -R` could undo
    without any error.
    """
    checks = [
        ("modify_production_gateway_config", GATEWAY_HOME / "agentbox/config.yaml"),
        ("enable_skill_bundle_production",
         (GATEWAY_HOME / "agentbox/skills" if agent_can("-e", str(GATEWAY_HOME / "agentbox/skills"))
          else GATEWAY_HOME / "skills")),
        ("modify_upstream_agent_source", GATEWAY_HOME / "hermes-agent-test"),
    ]
    bad = 0
    unknown = False
    for capability, path in checks:
        # Asked as the gateway user, because its home is mode 750 and the
        # operator cannot look inside it.
        if agent_can("-e", str(path)) is not True:
            continue
        writable = agent_can("-w", str(path))
        if writable is None:
            unknown = True
            continue
        if writable:
            bad += 1
            report(FAIL, f"{GATEWAY_USER} can write {path}, so '{capability}' is "
                         f"bypassable by shell regardless of its tier")
        else:
            report(OK, f"{capability} enforced ({path.name} not writable)")

    # Credential isolation rests on these two facts, and either could be
    # undone by one command without any error.
    if agent_can("-r", "/var/run/docker.sock"):
        bad += 1
        report(FAIL, f"{GATEWAY_USER} can reach the docker socket, so it can read "
                     f"every bridge credential with `docker inspect`")
    elif not unknown:
        report(OK, "docker socket unreachable to the assistant")

    operator_env = Path(env_dir())
    if agent_can("-r", str(operator_env)):
        bad += 1
        report(FAIL, f"{GATEWAY_USER} can read {operator_env}, which holds every "
                     f"bridge token and the memory review token")
    elif not unknown:
        report(OK, "operator credential directory unreadable to the assistant")

    if unknown:
        report(WARN, "could not test as the gateway user (needs passwordless "
                     "sudo); containment unverified")
    return bad


def bridge_readiness() -> int:
    """Report bridges whose upstream is unreachable."""
    bad = 0
    for service, port in BRIDGE_READY_PORTS.items():
        url = f"http://127.0.0.1:{port}/ready"
        try:
            with urllib.request.urlopen(url, timeout=READY_TIMEOUT) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                body = json.loads(exc.read())
                bridges = body.get("bridges") or {}
                bad_ones = {k: v for k, v in bridges.items() if v is not True}
                if bad_ones:
                    detail = "; ".join(f"{k}: {v}" for k, v in sorted(bad_ones.items()))
                else:
                    upstream = body.get("upstream") or {}
                    detail = "; ".join(str(v) for v in upstream.values()
                                       if isinstance(v, str))
            except Exception:  # noqa: BLE001 (diagnostics only)
                pass
            report(FAIL, f"{service} not ready (HTTP {exc.code}){': ' + detail if detail else ''}")
            bad += 1
            continue
        except TimeoutError:
            report(FAIL, f"{service} /ready timed out after {READY_TIMEOUT}s")
            bad += 1
            continue
        except urllib.error.URLError as exc:
            # Connection refused means nothing is listening, most likely
            # because the service is not deployed.
            if isinstance(exc.reason, ConnectionRefusedError):
                report(WARN, f"{service} /ready refused; is it deployed?")
                continue
            report(FAIL, f"{service} /ready unreachable: {exc.reason}")
            bad += 1
            continue
        except Exception as exc:  # noqa: BLE001
            report(FAIL, f"{service} /ready failed: {type(exc).__name__}")
            bad += 1
            continue
        bridges = payload.get("bridges")
        if isinstance(bridges, dict):
            # Name each bridge, so a failure reads "google: unreachable"
            # rather than "not ready".
            for bridge, state in sorted(bridges.items()):
                if state is True:
                    report(OK, f"{service}: {bridge} bridge ready")
                else:
                    report(FAIL, f"{service}: {bridge} bridge not ready ({state})")
                    bad += 1
            continue
        upstream = payload.get("upstream", payload.get("bridge"))
        if upstream is None:
            report(OK, f"{service} ready (no upstream)")
        else:
            report(OK, f"{service} ready; upstream reachable")
    return bad
