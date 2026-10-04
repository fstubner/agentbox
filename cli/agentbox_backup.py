"""`agentbox backup`: archive the task and memory stores, list archives, and
rehearse a restore before pruning old ones.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

BACKUP_DIR = Path(os.environ.get(
    "AGENTBOX_BACKUP_DIR",
    str(Path("~/.local/state/agentbox/backups").expanduser())))


def backup(keep: int = 14, report: Callable[[str, str], None] | None = None) -> int:
    """Archive the stateful services. Verifies the archive before pruning."""
    def _rep(level: str, msg: str) -> None:
        if report:
            report(level, msg)
        else:
            print(f"[{level.lower()}] {msg}")

    state_dir = Path(os.environ.get(
        "AGENT_CONTROL_PLANE_STATE_DIR",
        str(Path("~/.local/state/agentbox").expanduser())))
    stamp = subprocess.run(["date", "-u", "+%Y%m%dT%H%M%SZ"],
                           capture_output=True, text=True).stdout.strip()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    archive = BACKUP_DIR / f"agentbox-{stamp}.tar.gz"
    staging = BACKUP_DIR / f".staging-{stamp}"
    staging.mkdir(parents=True, exist_ok=True)
    staging.chmod(0o700)
    (staging / "vikunja").mkdir(parents=True, exist_ok=True)
    (staging / "memory").mkdir(parents=True, exist_ok=True)
    (staging / "hermes").mkdir(parents=True, exist_ok=True)
    (staging / "config").mkdir(parents=True, exist_ok=True)

    vikunja_src = state_dir / "vikunja"
    if vikunja_src.is_dir():
        subprocess.run(["cp", "-a", f"{vikunja_src}/.", str(staging / "vikunja")], check=False)
    else:
        _rep("WARN", f"vikunja state not found at {vikunja_src}")

    # Memory lives in a Docker volume, so it is read out through a short-lived
    # container, which hands ownership of the copies back to this user before
    # exiting. Otherwise the clean-up below cannot delete them, and staging
    # directories holding everyone's private memories would pile up.
    volume = os.environ.get("AGENTBOX_MEMORY_VOLUME", "memory-bridge_memory_data")
    proc = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{volume}:/src:ro",
         "-v", f"{staging / 'memory'}:/dst", "alpine", "sh", "-c",
         f"cp -a /src/. /dst/ && chown -R {os.getuid()}:{os.getgid()} /dst"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        _rep("WARN", f"memory volume not archived: {proc.stderr.strip()[:120]}")

    hermes_db = Path(os.environ.get(
        "AGENTBOX_HERMES_DB", "/home/agentbox/agentbox/state.db"))
    hermes_dst = staging / "hermes" / "state.db"
    can_read = False
    try:
        can_read = hermes_db.is_file()
    except (PermissionError, OSError):
        pass
    if not can_read:
        can_read = (subprocess.run(
            ["sudo", "-n", "-u", "agentbox", "test", "-f", str(hermes_db)],
            capture_output=True).returncode == 0)
    if can_read:
        backed_up = False
        try:
            s = sqlite3.connect(f"file:{hermes_db}?mode=ro", uri=True)
            d = sqlite3.connect(str(hermes_dst))
            s.backup(d)
            s.close()
            d.close()
            backed_up = True
        except (sqlite3.Error, PermissionError, OSError):
            pass
        if not backed_up:
            proc_h = subprocess.run(
                ["sudo", "-n", "python3", "-c",
                 f"import sqlite3; s=sqlite3.connect('{hermes_db}'); d=sqlite3.connect('{hermes_dst}'); "
                 f"s.backup(d); s.close(); d.close()"],
                capture_output=True, text=True)
            if proc_h.returncode == 0:
                subprocess.run(["sudo", "-n", "chown", f"{os.getuid()}:{os.getgid()}", str(hermes_dst)], check=False)
            else:
                _rep("WARN", f"hermes state.db backup failed: {proc_h.stderr.strip()[:120]}")
    else:
        _rep("WARN", f"hermes state.db not found at {hermes_db}")

    config_src = Path(os.environ.get(
        "AGENTBOX_ENV_DIR", str(Path("~/.config/agentbox").expanduser())))
    if config_src.is_dir():
        subprocess.run(["cp", "-a", f"{config_src}/.", str(staging / "config")], check=False)
    else:
        _rep("WARN", f"config directory not found at {config_src}")

    subprocess.run(["tar", "-czf", str(archive), "-C", str(staging), "."], check=False)
    # Private memories, so readable by the owner only.
    archive.chmod(0o600)

    # No ignore_errors. A cleanup that cannot run is the defect described
    # above, and it must be reported when it happens.
    try:
        shutil.rmtree(staging)
    except OSError as exc:
        _rep("FAIL", f"could not remove {staging}: {exc}. It holds an "
                     f"unencrypted copy of household memory. Delete it by hand.")
        return 1

    # Restore-check the archive we just made before touching the old ones.
    # This stops a prune from deleting the last good archive to make room for
    # a broken one.
    if restore_check(archive.name, report=report) != 0:
        _rep("FAIL", f"archive {archive.name} failed verification; keeping old backups")
        return 1

    # Keep the newest N archives.
    archives = sorted(BACKUP_DIR.glob("agentbox-*.tar.gz"), reverse=True)
    for stale in archives[keep:]:
        try:
            stale.unlink()
        except OSError as exc:
            _rep("WARN", f"could not remove old archive {stale}: {exc}")

    _rep("OK", f"archived state -> {archive.name} ({archive.stat().st_size // 1024} KB)")
    return 0


def restore_check(archive_name: str = "", report: Callable[[str, str], None] | None = None) -> int:
    """Rehearse a restore: unpack an archive and check it could rebuild state.

    Checks that the memory store parses and holds data and that Vikunja's data
    is present. It does not do a real restore. A real restore means stopping
    services and overwriting a volume, and stays the operator's decision.
    """
    def _rep(level: str, msg: str) -> None:
        if report:
            report(level, msg)
        else:
            print(f"[{level.lower()}] {msg}")

    archives = sorted(BACKUP_DIR.glob("agentbox-*.tar.gz"), reverse=True)
    if archive_name:
        archives = [a for a in archives if a.name == archive_name]
    if not archives:
        _rep("FAIL", f"no archive found in {BACKUP_DIR}")
        return 1
    archive = archives[0]

    with tempfile.TemporaryDirectory(prefix="agentbox-restore-check-") as tmp:
        target = Path(tmp)
        proc = subprocess.run(["tar", "-xzf", str(archive), "-C", str(target)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            _rep("FAIL", f"{archive.name} would not extract: "
                         f"{proc.stderr.strip()[:200]}")
            return 1

        problems = []
        memory = target / "memory" / "memory.json"
        if not memory.is_file():
            problems.append("no memory/memory.json in the archive")
        else:
            try:
                store = json.loads(memory.read_text(encoding="utf-8"))
            except ValueError as exc:
                problems.append(f"memory store does not parse: {exc}")
            else:
                # Check the shape as well as parsing. An empty dict is valid
                # JSON and would restore a household that remembers nothing.
                for key in ("memories", "proposals"):
                    if not isinstance(store.get(key), list):
                        problems.append(f"memory store has no '{key}' list")
                counts = {k: len(v) for k, v in store.items()
                          if isinstance(v, list)}
                _rep("OK", f"memory store restores: {counts}")

        vikunja = target / "vikunja"
        files = list(vikunja.rglob("*")) if vikunja.is_dir() else []
        if not any(f.is_file() for f in files):
            problems.append("vikunja directory is empty, so tasks would not "
                            "come back")
        else:
            _rep("OK", f"vikunja restores: {sum(1 for f in files if f.is_file())} file(s)")

        hermes_file = target / "hermes" / "state.db"
        if hermes_file.is_file():
            try:
                import sqlite3
                con = sqlite3.connect(str(hermes_file))
                msg_count = con.cursor().execute("SELECT count(*) FROM messages").fetchone()[0]
                con.close()
                _rep("OK", f"hermes restores: {msg_count} message(s)")
            except Exception as exc:
                problems.append(f"hermes state.db corrupt: {exc}")

        config_dir = target / "config"
        if config_dir.is_dir():
            env_files = list(config_dir.glob("*.env"))
            if env_files:
                _rep("OK", f"config restores: {len(env_files)} env file(s)")

        if problems:
            for problem in problems:
                _rep("FAIL", problem)
            _rep("FAIL", f"{archive.name} would NOT fully restore")
            return 1

    _rep("OK", f"{archive.name} verified: extracts, parses, and holds state")
    print("\nTo restore for real: stop the services, replace the memory volume "
          "and the vikunja state dir from this archive, then redeploy.\n"
          "See docs/runbook.md. This command does not restore.")
    return 0


def backup_list() -> int:
    archives = sorted(BACKUP_DIR.glob("agentbox-*.tar.gz"), reverse=True)
    if not archives:
        print(f"no backups in {BACKUP_DIR}")
        return 0
    for a in archives:
        print(f"{a.name}  {a.stat().st_size // 1024:6d} KB")
    print(f"\nrestore with: tar -xzf {archives[0]} -C <target>")
    return 0
