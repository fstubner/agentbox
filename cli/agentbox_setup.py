"""agentbox_setup — first-time machine setup and security hardening.

Follows the established agentbox modular CLI pattern:
- Dedicated module with clean functional boundaries (< 300 LOC)
- Automates directory creation, permission hardening (mode 0700/0750/2775),
  proposal sandbox setup, example env templates, and bubblewrap profile.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

BUILDER_GID = 65532  # Non-root group that the builder container writes as

SECRET_WRAPPER_EXAMPLE = """#!/bin/sh
# Agentbox Pluggable Secret Provider Wrapper
#
# To use an external secret manager (Infisical, Bitwarden, 1Password, Doppler, Vault):
# 1. Copy this file to 'secret-wrapper' in this directory (~/.config/agentbox/secret-wrapper).
# 2. Make it executable: chmod +x ~/.config/agentbox/secret-wrapper
# 3. Agentbox deploy will automatically delegate secret resolution through it.
#
# Example implementations:
#
# Infisical:
#   exec infisical run --env-file="$1" -- "${@:2}"
#
# Bitwarden Secrets Manager:
#   exec bws run -- "${@:2}"
#
# 1Password CLI:
#   exec op run --env-file="$1" -- "${@:2}"
#
# Doppler:
#   exec doppler run -- "${@:2}"
#
# Mozilla SOPS:
#   exec sops exec-env "$1" "${@:2}"
"""

ENV_EXAMPLE = """# Agentbox Service Environment Configuration
# Place service-specific environment files in this directory:
#   ~/.config/agentbox/<service>.env
#
# You can use secret references (resolved in RAM, zero plaintext on disk):
#   1Password: op://Agentbox/<service>/token
#   Infisical: infisical://<service>/token
#   Bitwarden: bws://<secret-id>
#   Doppler:   doppler://<token>
#
# Or standard plain key=value pairs if running in a fully-contained environment.
"""

SANDBOX_LAUNCHER = """#!/bin/sh
# Run an agent process sandboxed with Bubblewrap (bwrap) so it cannot read ~/.config
if ! command -v bwrap >/dev/null 2>&1; then
    echo "[warn] bwrap not found in PATH; running without bubblewrap sandbox" >&2
    exec "$@"
fi

STATE_DIR="${AGENT_CONTROL_PLANE_STATE_DIR:-$HOME/.local/state/agentbox}"

exec bwrap \\
    --ro-bind / / \\
    --dev /dev \\
    --proc /proc \\
    --tmpfs /tmp \\
    --tmpfs "$HOME/.config" \\
    --bind "$STATE_DIR" "$STATE_DIR" \\
    --chdir "$PWD" \\
    -- "$@"
"""


def setup_config_dir(config_dir: Path, report: Callable[[str, str], None]) -> int:
    """Create operator config directory locked down to mode 0700."""
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(config_dir, 0o700)
        report("OK", f"config directory secured: {config_dir} (mode 0700)")
        return 0
    except OSError as exc:
        report("FAIL", f"failed to secure config dir {config_dir}: {exc}")
        return 1


def setup_state_dirs(state_dir: Path, report: Callable[[str, str], None]) -> int:
    """Create state subdirectories (backups, logs, invites) mode 0750."""
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(state_dir, 0o750)
        for sub in ("backups", "logs", "invites", "portal"):
            p = state_dir / sub
            p.mkdir(parents=True, exist_ok=True)
            os.chmod(p, 0o750)
        report("OK", f"state directories initialized: {state_dir} (mode 0750)")
        return 0
    except OSError as exc:
        report("FAIL", f"failed to create state dirs in {state_dir}: {exc}")
        return 1


def setup_builder_repo(repo: Path, builder_repo: Path, report: Callable[[str, str], None]) -> int:
    """Clone and harden the proposal sandbox repository with group setgid."""
    try:
        if not (repo / ".git").exists():
            report("WARN", f"source repo is not a git repository: {repo}; skipping builder sandbox clone")
            return 0
        if not builder_repo.exists():
            res = subprocess.run(
                ["git", "clone", str(repo), str(builder_repo)],
                capture_output=True, text=True, check=False)
            if res.returncode != 0:
                report("FAIL", f"failed to clone builder-repo: {res.stderr.strip()}")
                return 1

        subprocess.run(
            ["git", "-C", str(builder_repo), "config", "core.sharedRepository", "group"],
            check=False, capture_output=True)
        subprocess.run(
            ["git", "-C", str(builder_repo), "remote", "set-url", "origin", "/origin"],
            check=False, capture_output=True)

        # Set permissions: 2775 for dirs, 0664 for files
        for root, dirs, files in os.walk(builder_repo):
            for d in dirs:
                try:
                    os.chmod(os.path.join(root, d), 0o2775)
                except OSError:
                    pass
            for f in files:
                try:
                    os.chmod(os.path.join(root, f), 0o664)
                except OSError:
                    pass

        # Attempt to chown group to 65532 if permitted
        try:
            for root, dirs, files in os.walk(builder_repo):
                for item in dirs + files:
                    full_path = os.path.join(root, item)
                    stat = os.stat(full_path)
                    os.chown(full_path, stat.st_uid, BUILDER_GID)
        except (OSError, PermissionError):
            pass  # Non-root cannot arbitrary chown group; setgid handles new files

        report("OK", f"builder proposal sandbox ready: {builder_repo} (setgid group)")
        return 0
    except OSError as exc:
        report("FAIL", f"builder-repo setup failed: {exc}")
        return 1


def setup_templates(config_dir: Path, report: Callable[[str, str], None]) -> int:
    """Install secret provider wrapper and env templates."""
    try:
        wrapper_example = config_dir / "secret-wrapper.example"
        if not wrapper_example.exists():
            wrapper_example.write_text(SECRET_WRAPPER_EXAMPLE, encoding="utf-8")

        env_example = config_dir / ".env.example"
        if not env_example.exists():
            env_example.write_text(ENV_EXAMPLE, encoding="utf-8")

        report("OK", f"secret provider templates installed in {config_dir}")
        return 0
    except OSError as exc:
        report("FAIL", f"failed to write templates in {config_dir}: {exc}")
        return 1


def setup_sandbox_launcher(repo: Path, report: Callable[[str, str], None]) -> int:
    """Create the bubblewrap sandboxing launcher in cli/agentbox-sandbox."""
    try:
        launcher_path = repo / "cli" / "agentbox-sandbox"
        launcher_path.write_text(SANDBOX_LAUNCHER, encoding="utf-8")
        os.chmod(launcher_path, 0o755)
        report("OK", f"bubblewrap sandbox launcher installed: {launcher_path}")
        return 0
    except OSError as exc:
        report("FAIL", f"failed to install sandbox launcher: {exc}")
        return 1


def setup(
    repo: Path,
    config_dir: Path | None = None,
    state_dir: Path | None = None,
    report: Callable[[str, str], None] | None = None,
) -> int:
    """Idempotently prepare this machine for agentbox operation."""
    if report is None:
        def report(level: str, msg: str) -> None:
            print(f"[{level.lower()}] {msg}")

    env_cfg = os.environ.get("AGENTBOX_ENV_DIR") or "~/.config/agentbox"
    config_dir = config_dir or Path(env_cfg).expanduser()
    env_st = os.environ.get("AGENT_CONTROL_PLANE_STATE_DIR") or "~/.local/state/agentbox"
    state_dir = state_dir or Path(env_st).expanduser()

    failures = 0
    failures += setup_config_dir(config_dir, report)
    failures += setup_state_dirs(state_dir, report)
    failures += setup_builder_repo(repo, state_dir / "builder-repo", report)
    failures += setup_templates(config_dir, report)
    failures += setup_sandbox_launcher(repo, report)

    if failures == 0:
        report("OK", "setup completed successfully; run `agentbox doctor` to verify containment")
    else:
        report("FAIL", f"setup finished with {failures} error(s)")

    return 1 if failures else 0
