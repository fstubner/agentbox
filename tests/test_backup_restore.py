"""Backup and restore — the one failure here that cannot be undone.

Neither had a single test until 2026-08-14, which is how the backup came to
leak an unremovable copy of every private memory on thirteen consecutive runs
without anyone noticing. `tar -tzf` proved the archive was readable; nothing
proved it held anything, and no restore had ever been rehearsed.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def cli(tmp_path, monkeypatch):
    loader = importlib.machinery.SourceFileLoader("abx_backup", str(REPO / "cli" / "agentbox_backup.py"))
    spec = importlib.util.spec_from_loader("abx_backup", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["abx_backup"] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "BACKUP_DIR", tmp_path / "backups")
    return module


def _archive(cli, memory: dict | None = None, vikunja: bool = True) -> Path:
    """Build an archive shaped like a real one."""
    cli.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    staging = cli.BACKUP_DIR / "build"
    (staging / "memory").mkdir(parents=True)
    (staging / "vikunja").mkdir(parents=True)
    if memory is not None:
        (staging / "memory" / "memory.json").write_text(json.dumps(memory))
    if vikunja:
        (staging / "vikunja" / "db").write_text("tasks")
    path = cli.BACKUP_DIR / "agentbox-20260814T000000Z.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        tar.add(staging, arcname=".")
    return path


def test_a_good_archive_verifies(cli, capsys):
    _archive(cli, {"memories": [{"statement": "Bin day is Wednesday"}],
                   "proposals": []})
    assert cli.restore_check() == 0
    out = capsys.readouterr().out
    assert "memory store restores" in out
    assert "vikunja restores" in out


def test_an_archive_with_unparseable_memory_fails(cli, capsys):
    cli.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    staging = cli.BACKUP_DIR / "build"
    (staging / "memory").mkdir(parents=True)
    (staging / "vikunja").mkdir(parents=True)
    (staging / "memory" / "memory.json").write_text("{corrupt")
    (staging / "vikunja" / "db").write_text("tasks")
    with tarfile.open(cli.BACKUP_DIR / "agentbox-20260814T000000Z.tar.gz", "w:gz") as tar:
        tar.add(staging, arcname=".")
    assert cli.restore_check() == 1
    assert "does not parse" in capsys.readouterr().out


def test_an_archive_missing_the_memory_store_fails(cli, capsys):
    _archive(cli, memory=None)
    assert cli.restore_check() == 1
    assert "no memory/memory.json" in capsys.readouterr().out


def test_an_archive_with_no_tasks_fails(cli, capsys):
    """Valid JSON that would restore a household with no tasks is not a
    successful restore."""
    _archive(cli, {"memories": [], "proposals": []}, vikunja=False)
    assert cli.restore_check() == 1
    assert "vikunja directory is empty" in capsys.readouterr().out


def test_a_memory_store_of_the_wrong_shape_fails(cli, capsys):
    """`{}` is valid JSON and would restore a household that remembers
    nothing. Parseability is not the property worth checking."""
    _archive(cli, {})
    assert cli.restore_check() == 1
    assert "has no 'memories' list" in capsys.readouterr().out


def test_no_archive_at_all_is_reported(cli, capsys):
    cli.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    assert cli.restore_check() == 1
    assert "no archive found" in capsys.readouterr().out


# --- the leak ------------------------------------------------------------------


def test_the_container_hands_the_files_back_to_this_user():
    """Files copied out of the volume landed owned by the container's uid, so
    the host could not delete them. Thirteen runs, thirteen orphaned staging
    directories, each an unencrypted copy of every private memory."""
    source = (REPO / "cli" / "agentbox_backup.py").read_text()
    block = source.split("def backup(")[1].split("\ndef ")[0]
    assert "chown -R" in block
    assert "os.getuid()" in block and "os.getgid()" in block


def _code_only(block: str) -> str:
    """Comment-free view of a block. See tests/conftest.py for why."""
    from conftest import strip_comments
    return strip_comments(block)


def test_cleanup_failure_is_reported_not_swallowed():
    """ignore_errors=True is why it hid for thirteen runs."""
    source = (REPO / "cli" / "agentbox_backup.py").read_text()
    block = _code_only(source.split("def backup(")[1].split("\ndef ")[0])
    assert "ignore_errors" not in block
    assert "could not remove" in block


def test_archives_and_staging_are_not_world_readable():
    """They hold private memories. Archives were 664 and the staging tree 644
    until 2026-08-14."""
    source = (REPO / "cli" / "agentbox_backup.py").read_text()
    block = source.split("def backup(")[1].split("\ndef ")[0]
    assert "archive.chmod(0o600)" in block
    assert "staging.chmod(0o700)" in block
