#!/usr/bin/env python3
"""Get a Google OAuth refresh token for google-workspace-bridge.

Standard library only. It runs Google's installed-app loopback flow: it starts
a local listener, prints an authorisation URL, waits for Google to redirect
back with a code, exchanges the code and prints the refresh token. The token
goes to stdout only, never to disk, for you to store in your secret manager.

Usage
-----
    python3 oauth-setup.py                 # reads client id and secret from 1Password
    python3 oauth-setup.py --client-id X --client-secret Y

Over SSH, the redirect lands on the server's loopback, so forward the port
from the machine with the browser.

    ssh -L 8899:127.0.0.1:8899 you@<host>

Then open the printed URL in your local browser. --port changes the port.

Once it prints the token, store it and redeploy.

    op item edit google-workspace-bridge refresh_token=<token> --vault Agentbox
    cli/agentbox deploy google-workspace-bridge
    cli/agentbox doctor          # google-workspace-bridge should read ready

If the token stops working within about a week, the OAuth consent screen is
probably still in Testing, where Google expires refresh tokens after 7 days.
Publishing the app (Google Cloud console, APIs & Services, OAuth consent
screen, Publish app) makes them last.
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

# What app/bridge.py calls.
#   gmail.modify  read messages and labels, mark read, archive, apply labels
#   calendar      list calendars, read events, free/busy, create events
#   drive.file    create and read only files this app created
# Keep it minimal, because every scope widens what the token can do.
SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive.file",
    # Read-only history of who changed what and when. It cannot alter
    # history or read file contents.
    "https://www.googleapis.com/auth/drive.activity.readonly",
    # Turns "someone edited the budget" into a name, since Drive Activity
    # returns a people/{id} and only the People API maps it to a person.
    #
    # It reads the contact list, which is a real widening: knowing who works on
    # a document versus knowing everyone you have emailed. Activity works
    # without it and says "someone" instead.
    "https://www.googleapis.com/auth/contacts.readonly",
    # Workspace domains only; silently returns nothing on a personal account,
    # where it costs nothing to have asked.
    "https://www.googleapis.com/auth/directory.readonly",
)

# Opt-in, and the most consequential choice in this file.
#
# drive.file (above) reads and writes only files the assistant created. Google
# enforces that, so it holds even if this bridge is compromised, but it cannot
# find your tenancy agreement because it cannot see it.
#
# drive.readonly reads every file in the drive, including tax returns, medical
# letters and contracts. That makes Drive search useful and is a large
# widening. Set GOOGLE_ENABLE_DRIVE_READ_ALL=1 to request it, deliberately.
#
# This is the credential's boundary, not a policy check. A scope that was never
# granted cannot be used at all.
if os.environ.get("GOOGLE_ENABLE_DRIVE_READ_ALL", "").strip() in ("1", "true", "yes"):
    SCOPES = SCOPES + ("https://www.googleapis.com/auth/drive.readonly",)


AUTH_FILES = (
    "~/.config/agentbox/1password.env",
    "~/.config/agentbox/1password.env",
)


def op_environment() -> dict[str, str]:
    """Load the 1Password service account token the way cli/agentbox does.

    Without OP_SERVICE_ACCOUNT_TOKEN, `op` asks to sign in on the terminal,
    which hangs invisibly inside a subprocess with captured output.
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
                 "  set -a; . ~/.config/agentbox/1password.env; set +a\n"
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
