"""The privileged half of onboarding: completing invites from the spool."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import agentbox_onboarding
from agentbox_accounts import exchange_oauth_code, provision_google_bridge
from agentbox_common import FAIL, OK, WARN, report
from agentbox_identity import identity_add

# --- completing an invite -------------------------------------------------------
#
# The privileged half of onboarding. `cli/agentbox-invite` collects answers on
# an unprivileged page, and this runs as the operator to do what needs real
# authority: creating a Vikunja user inside its container, issuing an identity
# token and wiring per-person routing. Vikunja registration is off, so creating
# an account needs `vikunja user create` in the container, a Docker-socket
# privilege that must never sit behind a web form.

INVITE_DIR = Path(os.environ.get(
    "AGENTBOX_INVITE_DIR",
    str(Path("~/.local/state/agentbox/invites").expanduser())))


def _invite(token_id: str) -> dict | None:
    try:
        return json.loads((INVITE_DIR / f"{token_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def provision_vikunja_user(name: str, password: str) -> bool:
    """Create the person's task account inside the Vikunja container.

    Registration is off (VIKUNJA_SERVICE_ENABLEREGISTRATION=false), so the
    HTTP API cannot do it, but the container's CLI can.
    """
    container = os.environ.get("AGENTBOX_VIKUNJA_CONTAINER", "vikunja-vikunja-1")
    probe = subprocess.run(["docker", "ps", "--filter", f"name={container}",
                            "--format", "{{.Names}}"],
                           capture_output=True, text=True, timeout=20)
    if container not in probe.stdout:
        report(WARN, f"vikunja container '{container}' not running; "
                     f"skipping task account")
        return False
    result = subprocess.run(
        ["docker", "exec", container, "/app/vikunja/vikunja", "user", "create",
         "--username", name, "--email", f"{name}@agentbox.local",
         "--password", password],
        capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:200]
        report(WARN, f"could not create the vikunja user: {detail}")
        return False
    return True


def invite_complete(token_id: str) -> int:
    """Turn a submitted invite into a working identity."""
    record = _invite(token_id)
    if not record:
        report(FAIL, f"no invite '{token_id}', check `agentbox-invite list`")
        return 1
    if not record.get("used_at"):
        report(FAIL, "that invite has not been filled in yet")
        return 1
    if record.get("completed_at"):
        report(FAIL, "that invite has already been completed")
        return 1

    name = record.get("identity", "")
    chosen = record.get("connectors", [])
    display = record.get("display_name", name)
    print(f"Completing invite for '{name}' ({display})")
    print(f"  requested: {', '.join(chosen) if chosen else 'nothing personal'}\n")

    if identity_add(name) != 0:
        return 1

    if "vikunja" in chosen:
        import secrets as _secrets
        password = _secrets.token_urlsafe(18)
        if provision_vikunja_user(name, password):
            report(OK, f"created vikunja user '{name}'")
            print(f"      one-time password: {password}")
            print("      Give her this once; she changes it on first login.")

    if "google" in chosen:
        code = record.get("google_code", "")
        redirect_uri = record.get("google_redirect_uri", "")
        if not code:
            report(WARN, "she chose google but did not finish the consent "
                         "screen, so there is no code to exchange")
            print("      Not a problem to re-invite for: she connects Google "
                  "herself from\n      the portal under 'Your accounts', "
                  "which provisions the same\n      bridge. Until she does, "
                  "she shares yours, which is wrong for mail.")
        else:
            refresh_token = exchange_oauth_code(code, redirect_uri)
            if refresh_token and provision_google_bridge(name, refresh_token):
                report(OK, f"provisioned {name}-google-bridge with her own "
                           f"credential")
                print(f"      Store it: op item edit Agentbox/google-{name} "
                      f"--refresh_token=<from "
                      f"{name}-google-bridge.env>")
                print("      They have no writable calendar yet. Set "
                      "GOOGLE_ALLOWED_WRITE_CALENDAR_ID in that env file "
                      "if she wants one.")

    record["completed_at"] = int(time.time())
    # The secret is spent; there is no reason to keep it on disk.
    record.pop("secret", None)
    path = INVITE_DIR / f"{token_id}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    report(OK, f"invite {token_id} completed")
    print("\nFinally: cli/agentbox deploy agentbox-mcp")
    return 0


def invite_drain() -> int:
    """Complete the invites an admin approved in the portal.

    The privileged half of onboarding, triggered by a file rather than by
    someone at a shell, so onboarding needs no terminal and the Docker socket
    stays off the web.

    Everything about whether to act is re-derived from the invite record. The
    request is trusted for one thing, which invite is meant, and `pending()`
    has already checked that against the request's filename.

    It runs unattended, so it never retries. A failure is settled with its
    reason, rather than staying queued and firing again on the next trigger.
    """
    onb = agentbox_onboarding
    requests = onb.pending()
    if not requests:
        return 0

    failed = 0
    for req in requests:
        token_id = req["token_id"]
        if onb.expired(req):
            onb.settle(req, "expired",
                       "sat in the spool longer than an approval stays a "
                       "decision")
            report(WARN, f"invite {token_id}: approval expired unused")
            continue

        record = _invite(token_id)
        if not record:
            onb.settle(req, "unknown", "no invite with that id")
            report(WARN, f"invite {token_id}: approved but no such invite")
            continue
        if not record.get("used_at"):
            onb.settle(req, "not_submitted", "she has not filled it in yet")
            report(WARN, f"invite {token_id}: approved before it was filled in")
            continue
        if record.get("completed_at"):
            # Two admins approving the same invite, or a retrigger, should
            # converge quietly rather than read as something going wrong.
            onb.settle(req, "already_completed", "completed before this ran")
            continue

        code = invite_complete(token_id)
        if code == 0:
            onb.settle(req, "completed",
                       f"approved by {req.get('requested_by', 'unknown')}")
        else:
            failed += 1
            onb.settle(req, "failed", f"`invite complete` exited {code}")
            report(FAIL, f"invite {token_id}: completion failed")
    return 1 if failed else 0
