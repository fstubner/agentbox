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

# Bridges that front a backing service expose /ready, which probes it. Their
# container healthcheck deliberately does not — see bridge_base's module
# docstring. Without this check a downed backing service is invisible: on
# 2026-07-31 Vikunja was down for hours while all six bridges reported healthy.
# One entry: the gateway's /ready probes every bridge over the container
# networks and names the ones that are not. Probing bridges from the host is no
# longer possible — they publish no host ports, which is the point: a leaked
# bridge token is unusable by anything outside the gateway's networks.
BRIDGE_READY_PORTS = {
    "agentbox-mcp": 3465,
}


# Must exceed the bridge's own upstream probe timeout (5s in bridge_base), or
# a hanging upstream trips this timeout first and the outage is misreported as
# "not deployed" — a warning, which would let doctor exit 0 during an outage.
READY_TIMEOUT = 10


def portal_email_delivery() -> int:
    """Warn when the portal can mint sign-in links but cannot deliver them.

    This failure is invisible by construction. The sign-in page must answer
    identically for a registered and an unregistered address, or it becomes a
    way to enumerate who lives here — so it cannot report that delivery failed.
    Somebody asks for a link, is told one is on its way, and nothing arrives.

    Found the hard way: the portal ran for a day with addresses configured and
    no SMTP host.
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
    # Email is one channel, not the only one. Discord DM delivery needs no
    # SMTP credential and no personal address in the From line, and it is safe
    # for the same reason email is: the link is bound to the browser that
    # requested it, so reading it is not enough to use it.
    channels = []
    if unit_env.get("AGENTBOX_SMTP_HOST"):
        channels.append("email")
    if unit_env.get("AGENTBOX_DISCORD_IDENTITIES"):
        channels.append("Discord DM")
    if channels:
        report(OK, f"portal can deliver sign-in links by "
                   f"{' and '.join(channels)}")
        return 0
    report(WARN, "portal has addresses configured but no delivery channel — "
                 "sign-in links are minted and reach nobody, and the page "
                 "cannot say so because answering differently for a registered "
                 "address would reveal who lives here. Set AGENTBOX_SMTP_HOST "
                 "or AGENTBOX_DISCORD_IDENTITIES, or hand links over with: "
                 "cli/agentbox-portal link <name>")
    return 0


def portal_redirect_uri() -> int:
    """Fail early if the portal's OAuth redirect is one Google will refuse.

    Registering it is a console step nobody can automate, so the least this can
    do is not send an operator to register something impossible. It sent them
    twice: both the invite page and the portal documented an agentbox.local
    URI, which Google rejects outright.
    """
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
                     f"or a real public domain over https — .local and bare "
                     f"hostnames are refused by the console.")
        return 1
    return 0


def connector_credentials() -> int:
    """Check each identity's Google credential still works.

    A refresh token revoked at Google leaves this box holding a dead
    credential, and nothing notices: /health stays green because the container
    is fine, and the first symptom is an opaque 403 during an unrelated task
    days later. That exact failure already happened once on the shared bridge,
    which is why the bridge grew an upstream_status probe — this extends it to
    the per-identity bridges, which nothing was checking.

    Warns rather than fails: a disconnected account is a legitimate state, not
    a broken deployment. The point is that it should be *visible*.
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
            report(WARN, f"{identity}'s google credential looks dead — they "
                         f"can reconnect at the portal, then run: "
                         f"cli/agentbox identity reconnect {identity}")
    # Always zero. A disconnected account is a legitimate state rather than a
    # broken deployment, so this reports and never fails the run.
    return 0


def assistant_containment() -> int:
    """Verify the assistant still cannot rewrite what constrains it.

    Three policy tiers were bypassable until 2026-08-05 because the gateway
    could write its own config, skills and source — enforced against tool calls
    while a shell sat beside them. The fix was ownership, and ownership is
    exactly the kind of thing a later `chown -R` undoes without comment.
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
        # Asked as the gateway user throughout: its home is mode 750, so
        # the operator cannot even stat inside it — Path.exists() raises.
        if agent_can("-e", str(path)) is not True:
            continue
        writable = agent_can("-w", str(path))
        if writable is None:
            unknown = True
            continue
        if writable:
            bad += 1
            report(FAIL, f"{GATEWAY_USER} can write {path} — '{capability}' is "
                         f"bypassable by shell regardless of its tier")
        else:
            report(OK, f"{capability} enforced ({path.name} not writable)")

    # Credential isolation currently rests on these two facts, not on the
    # architecture. Both are one command away from silently disappearing.
    if agent_can("-r", "/var/run/docker.sock"):
        bad += 1
        report(FAIL, f"{GATEWAY_USER} can reach the docker socket — it can read "
                     f"every bridge credential with `docker inspect`")
    elif not unknown:
        report(OK, "docker socket unreachable to the assistant")

    operator_env = Path(env_dir())
    if agent_can("-r", str(operator_env)):
        bad += 1
        report(FAIL, f"{GATEWAY_USER} can read {operator_env} — every bridge "
                     f"token and the memory review token are in there")
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
            except Exception:  # noqa: BLE001 — diagnostics only
                pass
            report(FAIL, f"{service} not ready (HTTP {exc.code}){': ' + detail if detail else ''}")
            bad += 1
            continue
        except TimeoutError:
            report(FAIL, f"{service} /ready timed out after {READY_TIMEOUT}s")
            bad += 1
            continue
        except urllib.error.URLError as exc:
            # Connection refused means nothing is listening — most likely the
            # service is simply not deployed, which is not a readiness failure.
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
            # The gateway's aggregate: name each bridge individually so a
            # failure reads "google: unreachable", not "gateway not ready".
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
