"""`agentbox deploy` and `agentbox update`."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from agentbox_common import FAIL, OK, REPO, WARN, report
from agentbox_policy import policy_sync, source_sha
from agentbox_validate import validate

# --- deploy -----------------------------------------------------------------

SERVICE_ALIASES = {
    "home-assistant": "homeassistant",
}


def deploy(service: str, dry_run: bool = False, pull: bool = False) -> int:
    """Validate, then bring up services/compose/<service> with its env file.

    Env file: $AGENTBOX_ENV_DIR/<service>.env (default ~/.config/agentbox,
    default ~/.config/agentbox).
    op:// references are resolved through the 1Password CLI without printing
    secret values. Compose project name is the service name.
    """
    service = SERVICE_ALIASES.get(service, service)
    compose_dir = REPO / "services" / "compose" / service
    if not (compose_dir / "compose.yaml").exists():
        report(FAIL, f"no compose file for service: {service}")
        return 1
    if validate() != 0:
        return 1

    # The policy every bridge enforces travels on the read-only mount rather
    # than inside the image. Synced here so a deploy can never start a
    # container against a policy older than the one committed.
    policy_sync(quiet=True)

    env_dir = os.environ.get("AGENTBOX_ENV_DIR", str(Path("~/.config/agentbox").expanduser()))
    env_file = Path(env_dir) / f"{service}.env"

    env = dict(os.environ)
    env.setdefault("AGENT_CONTROL_PLANE_STATE_DIR",
                   str(Path("~/.local/state/agentbox").expanduser()))
    env["AGENTBOX_SOURCE_SHA"] = source_sha(service)
    project = service

    compose = ["docker", "compose", "-p", project, "-f", "compose.yaml"]
    # --build is not optional: without it compose reuses the existing image and
    # the deploy reports success having shipped nothing but the git commit.
    compose_cmd = (compose + ["config", "--no-interpolate"] if dry_run
                   else compose + ["up", "-d", "--build"])

    if env_file.exists():
        text = env_file.read_text(encoding="utf-8")
        custom_wrapper = os.environ.get("AGENTBOX_SECRET_WRAPPER") or (
            str(Path(env_dir) / "secret-wrapper")
            if (Path(env_dir) / "secret-wrapper").is_file()
            and os.access(Path(env_dir) / "secret-wrapper", os.X_OK)
            else None
        )
        if custom_wrapper:
            compose_cmd = [custom_wrapper, str(env_file)] + compose_cmd
        elif "op://" in text:
            if not shutil.which("op"):
                report(FAIL, "env file uses op:// references but 1Password CLI is missing")
                return 1
            auth_file = Path(env_dir) / "1password.env"
            if auth_file.exists():
                for line in auth_file.read_text(encoding="utf-8").splitlines():
                    if "=" in line and not line.lstrip().startswith("#"):
                        key, _, value = line.partition("=")
                        env.setdefault(key.strip(), value.strip())
            compose_cmd = ["op", "run", "--env-file", str(env_file), "--"] + compose_cmd
        elif "infisical://" in text:
            if not shutil.which("infisical"):
                report(FAIL, "env file uses infisical:// references but infisical CLI is missing")
                return 1
            compose_cmd = ["infisical", "run", f"--env-file={env_file}", "--"] + compose_cmd
        elif "bws://" in text:
            if not shutil.which("bws"):
                report(FAIL, "env file uses bws:// references but bws CLI is missing")
                return 1
            compose_cmd = ["bws", "run", "--"] + compose_cmd
        elif "doppler://" in text:
            if not shutil.which("doppler"):
                report(FAIL, "env file uses doppler:// references but doppler CLI is missing")
                return 1
            compose_cmd = ["doppler", "run", "--"] + compose_cmd
        else:
            for line in text.splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, _, value = line.partition("=")
                    env.setdefault(key.strip(), value.strip())
    else:
        report(WARN, f"no env file at {env_file}; deploying without service env")

    if pull:
        # The same environment resolution as a deploy, because `docker compose
        # pull` also has to fill in the secrets in compose.yaml.
        pull_cmd = compose_cmd[:-3] if compose_cmd[-3:] == ["up", "-d", "--build"] \
            else compose_cmd
        pull_cmd = [c for c in pull_cmd if c not in ("up", "-d", "--build")] + ["pull"]
        pulled = subprocess.run(pull_cmd, cwd=compose_dir, env=env,
                                capture_output=True, text=True, timeout=1800)
        if pulled.returncode != 0:
            report(FAIL, f"pull failed: "
                         f"{(pulled.stderr or pulled.stdout).strip()[-300:]}")
            return 1
        report(OK, f"pulled latest images for {service}")

    proc = subprocess.run(compose_cmd, cwd=compose_dir, env=env)
    report(OK if proc.returncode == 0 else FAIL, f"deploy {service} finished")
    return proc.returncode


def update(service: str) -> int:
    """Pull a newer third-party image and redeploy that service.

    Separate from `deploy`, which ships your change and nothing else. If it
    also pulled upstream releases, a one-line config fix could move Home
    Assistant a version, and a failure would have two possible causes.
    `docker compose up` reuses a cached image indefinitely, so floating tags
    only move when this runs.
    """
    service = SERVICE_ALIASES.get(service, service)
    if not (REPO / "services" / "compose" / service / "compose.yaml").is_file():
        report(FAIL, f"no compose file for '{service}'")
        return 1
    return deploy(service, pull=True)
