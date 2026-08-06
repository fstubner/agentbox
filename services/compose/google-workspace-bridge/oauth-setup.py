#!/usr/bin/env python3
"""Mint a Google OAuth refresh token for google-workspace-bridge.

Stdlib only, matching the rest of the platform. Runs the installed-app
loopback flow: starts a local listener, prints an authorisation URL, waits for
Google to redirect back with a code, exchanges it, and prints the refresh
token. The token is written to stdout only — never to disk — so you can paste
it into 1Password yourself.

Usage
-----
    python3 oauth-setup.py                 # reads client id/secret from 1Password
    python3 oauth-setup.py --client-id X --client-secret Y

Working over SSH? The redirect lands on the *server's* loopback, so forward the
port from the machine with the browser:

    ssh -L 8899:127.0.0.1:8899 alex@<host>

then open the printed URL in your local browser. Use --port to change it.

After it prints the token, update 1Password:

    op item edit google-workspace-bridge refresh_token=<token> --vault Agentbox
    cli/agentbox deploy google-workspace-bridge
    cli/agentbox doctor          # google-workspace-bridge should read ready

If the token stops working again within about a week, the OAuth consent screen
is almost certainly still in "Testing" publishing status, where Google expires
refresh tokens after 7 days. Publishing the app (Google Cloud console → APIs &
Services → OAuth consent screen → Publish app) makes them durable. Re-minting
without changing that just resets the clock.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"

# Derived from what app/bridge.py actually calls:
#   gmail.modify  — read messages/labels, mark read, archive, apply labels
#   calendar      — list calendars, read events, freebusy, create events
#   drive.file    — create and read *only files this app created*
# Keep this list minimal; widening it widens the blast radius of the token.
SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.file",
    # Read-only audit trail: who changed what, when. Cannot alter history, and
    # cannot read file *contents* — a narrower thing than it sounds.
    "https://www.googleapis.com/auth/drive.activity.readonly",
)

# Opt-in, and the single most consequential choice in this file.
#
# drive.file (above) lets the assistant read and write the files it created and
# nothing else — a boundary Google enforces, so it holds even if this bridge is
# compromised. It cannot answer "find my tenancy agreement", because it cannot
# see it.
#
# drive.readonly lets it read every file in the drive. That is what makes Drive
# search useful and it is a genuinely large widening: tax returns, medical
# letters, contracts. Set GOOGLE_ENABLE_DRIVE_READ_ALL=1 to request it, having
# decided that on purpose.
#
# Note this is the *credential's* boundary, not a policy check. An approval
# prompt on a tool call only helps if a human reads carefully every time; a
# scope that was never granted cannot be spent at all.
if os.environ.get("GOOGLE_ENABLE_DRIVE_READ_ALL", "").strip() in ("1", "true", "yes"):
    SCOPES = SCOPES + ("https://www.googleapis.com/auth/drive.readonly",)


AUTH_FILES = (
    "~/.config/agentbox/1password.env",
    "~/.config/agent-control-plane/1password.env",
)


def op_environment() -> dict[str, str]:
    """Load the 1Password service account token the way cli/agentbox does.

    Without OP_SERVICE_ACCOUNT_TOKEN, `op` tries to authenticate interactively
    and prompts on the terminal — which a subprocess with captured output turns
    into an invisible hang. Sourcing the file here means the caller does not
    have to remember to, which is a documented footgun that has already caused
    a session to wrongly conclude 1Password was broken.
    """
    env = dict(os.environ)
    if env.get("OP_SERVICE_ACCOUNT_TOKEN"):
        return env
    for candidate in AUTH_FILES:
        path = Path(candidate).expanduser()
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                env.setdefault(key.strip(), value.strip().strip("'\""))
        break
    return env


def from_1password(field: str) -> str:
    ref = f"op://Agentbox/google-workspace-mcp/{field}"
    env = op_environment()
    if not env.get("OP_SERVICE_ACCOUNT_TOKEN"):
        sys.exit("OP_SERVICE_ACCOUNT_TOKEN is not set and no 1password.env was found.\n"
                 "Source it first:\n"
                 "  set -a; . ~/.config/agent-control-plane/1password.env; set +a\n"
                 "or pass --client-id/--client-secret directly.")
    try:
        out = subprocess.run(["op", "read", ref], capture_output=True, text=True,
                             timeout=30, env=env, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        sys.exit("1Password CLI (op) not found — pass --client-id/--client-secret instead")
    except subprocess.TimeoutExpired:
        sys.exit(f"timed out reading {ref} — is the service account token valid?")
    if out.returncode != 0:
        sys.exit(f"could not read {ref}: {out.stderr.strip()}")
    return out.stdout.strip()


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    code: str | None = None
    error: str | None = None

    def do_GET(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        CallbackHandler.code = (query.get("code") or [None])[0]
        CallbackHandler.error = (query.get("error") or [None])[0]
        body = (b"Authorisation received. You can close this tab and return to the terminal."
                if CallbackHandler.code else
                b"Authorisation failed. Check the terminal.")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        """Silence the default access log; it would print the code."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client-id")
    parser.add_argument("--client-secret")
    parser.add_argument("--port", type=int, default=8899,
                        help="loopback port for the redirect (default: 8899, chosen to avoid the router on 8765)")
    args = parser.parse_args()

    client_id = args.client_id or from_1password("client_id")
    client_secret = args.client_secret or from_1password("client_secret")
    redirect_uri = f"http://127.0.0.1:{args.port}"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        # Required to get a refresh token back: without access_type=offline
        # Google returns only an access token, and without prompt=consent it
        # may skip re-issuing a refresh token for an already-consented client.
        "access_type": "offline",
        "prompt": "consent",
    }
    print("1. Ensure this exact redirect URI is registered on the OAuth client:")
    print(f"     {redirect_uri}")
    print("   (Google Cloud console -> Credentials -> your OAuth 2.0 Client ID)")
    print()
    print("2. Open this URL in a browser:")
    print(f"     {AUTH_URI}?{urllib.parse.urlencode(params)}")
    print()
    print(f"   Over SSH? First: ssh -L {args.port}:127.0.0.1:{args.port} <user>@<host>")
    print()
    print(f"Waiting for the redirect on {redirect_uri} ...")

    server = http.server.HTTPServer(("127.0.0.1", args.port), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        while CallbackHandler.code is None and CallbackHandler.error is None:
            threading.Event().wait(0.5)
    except KeyboardInterrupt:
        return 1
    finally:
        server.shutdown()

    if CallbackHandler.error:
        sys.exit(f"authorisation failed: {CallbackHandler.error}")

    data = urllib.parse.urlencode({
        "code": CallbackHandler.code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    request = urllib.request.Request(TOKEN_URI, data=data)
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        sys.exit(f"token exchange failed: HTTP {exc.code} {exc.read().decode()[:300]}")

    refresh = payload.get("refresh_token")
    if not refresh:
        sys.exit("no refresh_token in the response — retry with prompt=consent "
                 "(already set here) and confirm the client is an installed/desktop app")

    print()
    print("refresh_token:")
    print(refresh)
    print()
    print("Store it, then redeploy:")
    print("  op item edit google-workspace-bridge refresh_token='<token>' --vault Agentbox")
    print("  cli/agentbox deploy google-workspace-bridge")
    print("  cli/agentbox doctor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
