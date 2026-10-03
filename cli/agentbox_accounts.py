"""Each person's connected accounts: Google consent, provisioning, reconnect and revoke."""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from agentbox_common import FAIL, OK, REPO, WARN, env_dir, report, service_env_values
from agentbox_deploy import deploy
from agentbox_identity import read_env_file, write_env_value


def exchange_oauth_code(code: str, redirect_uri: str) -> str:
    """Exchange the authorisation code from the onboarding page for a refresh
    token, using the client secret the page never had.

    The page can start Google's consent and receive the code, which is useless
    on its own. The exchange needs the client secret, which stays on the
    operator side. Codes expire after about ten minutes, so `invite complete`
    must run while the person is still there.
    """
    values = service_env_values("google-workspace-bridge",
                                ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"))
    client_id = values.get("GOOGLE_CLIENT_ID", "")
    client_secret = values.get("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        report(FAIL, "google client id/secret not resolvable; cannot exchange "
                     "the authorisation code")
        return ""
    body = urllib.parse.urlencode({
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    }).encode()
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        report(FAIL, f"google refused the code exchange: {detail}")
        if "invalid_grant" in detail:
            print("      Authorisation codes expire in ~10 minutes and are "
                  "single use.\n      Issue a fresh invite and complete it "
                  "promptly.")
        return ""
    except urllib.error.URLError as exc:
        report(FAIL, f"could not reach google: {exc.reason}")
        return ""
    token = payload.get("refresh_token", "")
    if not token:
        report(FAIL, "google returned no refresh token — the consent screen "
                     "was probably already granted for this account without "
                     "prompt=consent")
    return token


def provision_google_bridge(identity: str, refresh_token: str) -> bool:
    """Stand up a Google bridge holding only this person's credential.

    Her mail is then reached with her token and nobody else's, which is the
    whole reason for a second container rather than a second credential in the
    first one.
    """
    import secrets as _secrets
    bridge_token = _secrets.token_hex(32)
    shared = service_env_values("google-workspace-bridge",
                                ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
                                 "GOOGLE_OWNED_LABEL_PREFIX"))
    env_path = Path(env_dir()) / f"{identity}-google-bridge.env"
    env_path.write_text("\n".join([
        f"IDENTITY_NAME={identity}",
        f"GOOGLE_CLIENT_ID={shared.get('GOOGLE_CLIENT_ID', '')}",
        f"GOOGLE_CLIENT_SECRET={shared.get('GOOGLE_CLIENT_SECRET', '')}",
        f"GOOGLE_REFRESH_TOKEN={refresh_token}",
        f"GOOGLE_BRIDGE_TOKEN={bridge_token}",
        f"GOOGLE_OWNED_LABEL_PREFIX={shared.get('GOOGLE_OWNED_LABEL_PREFIX', 'agentbox/')}",
        # Deliberately blank: she gets no writable calendar until an operator
        # picks one. Inheriting Alex's would let her assistant write to his.
        "GOOGLE_ALLOWED_WRITE_CALENDAR_ID=",
    ]) + "\n", encoding="utf-8")
    env_path.chmod(0o600)

    compose = (REPO / "services" / "compose" / "google-workspace-bridge"
               / "identity.compose.yaml")
    environment = {**os.environ, **read_env_file(env_path)}
    environment.setdefault("HOME", str(Path.home()))
    result = subprocess.run(
        ["docker", "compose", "-p", f"{identity}-google-bridge",
         "-f", str(compose), "up", "-d"],
        capture_output=True, text=True, timeout=300, env=environment, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:300]
        report(FAIL, f"could not start {identity}'s google bridge: {detail}")
        return False

    # Route this identity's Google calls to it. Everything else still falls
    # back to the shared bridge, which is correct for shared services.
    suffix = identity.upper().replace("-", "_")
    gateway_env = Path(env_dir()) / "agentbox-mcp.env"
    write_env_value(gateway_env, f"GOOGLE_BRIDGE_URL_{suffix}",
                    f"http://{identity}-google-bridge:8080")
    write_env_value(gateway_env, f"GOOGLE_BRIDGE_TOKEN_{suffix}", bridge_token)
    return gateway_routing_applied(identity)


def gateway_routing_applied(identity: str) -> bool:
    """Make the gateway use the routing just written.

    The gateway reads its environment when its container starts, so writing
    the env file is not enough. Until it is recreated, calls keep going to the
    shared bridge with whatever credential that holds, while the reconnect
    appears to have worked. A failure here is reported, never swallowed.
    """
    suffix = identity.upper().replace("-", "_")
    expected = f"http://{identity}-google-bridge:8080"
    gateway_env = Path(env_dir()) / "agentbox-mcp.env"

    def running_value(name: str) -> str:
        out = subprocess.run(
            ["docker", "exec", "agentbox-mcp-agentbox-mcp-1", "printenv", name],
            capture_output=True, text=True, timeout=30, check=False)
        return out.stdout.strip() if out.returncode == 0 else ""

    # Both the URL and the token must match what is on disk. A reconnect gives
    # the person's bridge a new token at the same URL, so checking only the URL
    # would leave the gateway with the old token and every call failing 401.
    on_disk = read_env_file(gateway_env)
    token_matches = (running_value(f"GOOGLE_BRIDGE_TOKEN_{suffix}")
                     == on_disk.get(f"GOOGLE_BRIDGE_TOKEN_{suffix}", ""))
    if running_value(f"GOOGLE_BRIDGE_URL_{suffix}") == expected and token_matches:
        return True  # already routing correctly; no need to disturb the gateway

    report(WARN, "restarting the gateway so it picks up the new routing")
    if deploy("agentbox-mcp") != 0:
        report(FAIL, f"{identity}'s credential was updated but the gateway "
                     f"still routes to the shared bridge. Their calls will use "
                     f"the wrong account until you run: "
                     f"cli/agentbox deploy agentbox-mcp")
        return False
    return True


def portal_requests(identity: str = "") -> list[dict]:
    """Connector requests people raised in the portal, oldest first."""
    directory = Path(os.environ.get(
        "AGENTBOX_PORTAL_DIR",
        os.path.expanduser("~/.local/state/agentbox/portal"))) / "requests"
    if not directory.is_dir():
        return []
    out = []
    for entry in sorted(directory.glob("*.json")):
        try:
            record = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        record["_path"] = str(entry)
        if record.get("completed_at"):
            continue
        if identity and record.get("identity") != identity:
            continue
        out.append(record)
    return out


def _mark_request_done(record: dict) -> None:
    path = Path(record.pop("_path", ""))
    if not path:
        return
    record["completed_at"] = int(time.time())
    record.pop("code", None)          # spent, and no reason to keep it on disk
    try:
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except OSError:
        pass


def revoke_google_token(refresh_token: str) -> bool:
    """Tell Google to invalidate the credential.

    Deleting our copy is not disconnecting: the grant would still be listed in
    the person's Google account, and anyone who had captured the token could
    still spend it. Revoking upstream is the part that actually ends access.
    """
    if not refresh_token:
        return False
    body = urllib.parse.urlencode({"token": refresh_token}).encode()
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/revoke", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=20):
            return True
    except urllib.error.HTTPError as exc:
        # 400 invalid_token means it was already dead, which is the goal.
        return exc.code == 400
    except Exception:  # noqa: BLE001
        return False


def connectors_sync(quiet: bool = False) -> int:
    """Complete every connector request people raised in the portal.

    The portal must not hold the Docker socket, so the privileged half runs
    here, on a timer, as the operator. That way a person can reconnect their
    own account without waiting for anyone.

    Google expires authorisation codes after about ten minutes, so the timer
    runs every thirty seconds.
    """
    pending = portal_requests()
    if not pending:
        if not quiet:
            report(OK, "no connector requests waiting")
        return 0
    failures = 0
    for record in pending:
        identity = str(record.get("identity", ""))
        action = str(record.get("action", ""))
        if not identity:
            continue
        if action == "reconnect" and record.get("code"):
            if identity_reconnect(identity) != 0:
                failures += 1
        elif action == "disconnect":
            if identity_disconnect(identity) != 0:
                failures += 1
    if failures:
        report(WARN, f"{failures} connector request(s) could not be completed")
    return 0


def identity_reconnect(name: str) -> int:
    """Finish a reconnect the portal started.

    The portal captured an authorisation code; this exchanges it with the
    client secret, which is why the two halves exist. Codes expire in about
    ten minutes.
    """
    pending = [r for r in portal_requests(name)
               if r.get("action") == "reconnect" and r.get("code")]
    if not pending:
        report(WARN, f"no pending reconnect for {name}; they start one at "
                     f"the portal under 'Your accounts'")
        return 1
    record = pending[-1]
    age = int(time.time()) - int(record.get("created_at", 0))
    if age > 600:
        report(FAIL, f"that authorisation code is {age // 60} minutes old and "
                     f"Google expires them at about ten. Ask {name} to press "
                     f"Reconnect again.")
        return 1
    token = exchange_oauth_code(record["code"], record.get("redirect_uri", ""))
    if not token:
        return 1
    if not provision_google_bridge(name, token):
        return 1
    _mark_request_done(record)
    report(OK, f"reconnected {name}'s Google account")
    report(OK, "run: cli/agentbox deploy agentbox-mcp   (to pick up routing)")
    return 0


def identity_disconnect(name: str) -> int:
    """Revoke a person's Google credential and remove it from this box."""
    env_path = Path(env_dir()) / f"{name}-google-bridge.env"
    if not env_path.exists():
        report(WARN, f"{name} has no Google bridge of their own; nothing to "
                     f"disconnect")
        return 1
    values = read_env_file(env_path)
    if revoke_google_token(values.get("GOOGLE_REFRESH_TOKEN", "")):
        report(OK, "credential revoked at Google")
    else:
        report(WARN, "could not confirm revocation at Google; removing the "
                     "local copy anyway, but check the account's connected "
                     "apps by hand")
    subprocess.run(["docker", "compose", "-p", f"{name}-google-bridge", "down"],
                   capture_output=True, text=True, timeout=120, check=False)
    try:
        env_path.unlink()
    except OSError:
        pass
    suffix = name.upper().replace("-", "_")
    gateway_env = Path(env_dir()) / "agentbox-mcp.env"
    for key in (f"GOOGLE_BRIDGE_URL_{suffix}", f"GOOGLE_BRIDGE_TOKEN_{suffix}"):
        write_env_value(gateway_env, key, "")
    for record in portal_requests(name):
        if record.get("action") == "disconnect":
            _mark_request_done(record)
    report(OK, f"disconnected {name}'s Google account")
    report(OK, "run: cli/agentbox deploy agentbox-mcp")
    return 0
