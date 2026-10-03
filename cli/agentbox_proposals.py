"""Reviewing the branches the builder proposes."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from agentbox_common import FAIL, OK, REPO, WARN, report

# --- reviewing the assistant's proposals -------------------------------------
#
# The builder writes branches into its own clone and never pushes, so this is
# how they reach the operator: fetch from the clone, read the diff, merge if
# it's good. Pull rather than push means that container never needs write
# access to this repository.

BUILDER_REPO = Path(os.environ.get(
    "AGENTBOX_BUILDER_REPO",
    str(Path("~/.local/state/agentbox/builder-repo").expanduser())))
PROPOSAL_REFSPEC = "refs/heads/proposal/*:refs/remotes/assistant/*"


def builder_git(*args: str, capture: bool = True) -> subprocess.CompletedProcess:
    """git in the operator's repository, reading proposals from the builder's
    clone.

    The clone is owned by the operator with group 65532, setgid and group
    writable, like policy-state and logs. Owning it by the container's uid
    instead makes git refuse to read it from here, and `-c safe.directory`
    cannot override that from the command line, by design.
    """
    return subprocess.run(["git", *args], cwd=str(REPO), text=True,
                          capture_output=capture, timeout=120)


def proposals_fetch() -> bool:
    if not (BUILDER_REPO / ".git").is_dir():
        report(WARN, f"no builder clone at {BUILDER_REPO}; "
                     "deploy builder-bridge first")
        return False
    result = builder_git("fetch", "--prune", str(BUILDER_REPO), PROPOSAL_REFSPEC)
    if result.returncode != 0:
        report(FAIL, f"could not fetch proposals: {result.stderr.strip()[:200]}")
        return False
    return True


def proposals_list() -> int:
    """What the assistant is waiting for you to look at."""
    if not proposals_fetch():
        return 1
    result = builder_git(
        "for-each-ref", "--sort=-committerdate",
        "--format=%(refname:short)\t%(committerdate:relative)\t%(contents:subject)",
        "refs/remotes/assistant/")
    rows = [line for line in result.stdout.splitlines() if line.strip()]
    if not rows:
        print("no proposals")
        return 0
    for row in rows:
        name, _, rest = row.partition("\t")
        when, _, subject = rest.partition("\t")
        print(f"{name.replace('assistant/', ''):<32} {when:<16} {subject}")
    print(f"\n{len(rows)} proposal(s), review with: "
          f"cli/agentbox proposals show <name>")
    return 0


def proposals_show(name: str) -> int:
    if not proposals_fetch():
        return 1
    ref = f"assistant/{name.removeprefix('proposal/')}"
    verify = builder_git("rev-parse", "--verify", f"{ref}^{{commit}}")
    if verify.returncode != 0:
        report(FAIL, f"no such proposal: {name}")
        return 1
    # Straight to stdout so it pages and colours like any other git diff.
    builder_git("log", "-1", "--stat", ref, capture=False)
    print()
    builder_git("diff", f"main...{ref}", capture=False)
    print(f"\nmerge with: cli/agentbox proposals merge {name}")
    return 0


def proposals_merge(name: str) -> int:
    """Merge a proposal onto a local branch, never straight onto main.

    The assistant cannot merge, and this keeps it that way. The work lands on a
    branch for you to test and push. Merging straight to main would make
    approval one keystroke on an unread diff.
    """
    if not proposals_fetch():
        return 1
    short = name.removeprefix("proposal/")
    ref = f"assistant/{short}"
    if builder_git("rev-parse", "--verify", f"{ref}^{{commit}}").returncode != 0:
        report(FAIL, f"no such proposal: {name}")
        return 1
    dirty = builder_git("status", "--porcelain").stdout.strip()
    if dirty:
        report(FAIL, "working tree is not clean; commit or stash first")
        return 1
    branch = f"review/{short}"
    created = builder_git("checkout", "-b", branch, ref)
    if created.returncode != 0:
        report(FAIL, created.stderr.strip()[:200])
        return 1
    report(OK, f"checked out {branch} from the assistant's proposal")
    print("\nReview it, run `cli/agentbox validate` and the tests, then merge to\n"
          "main yourself. Nothing has been deployed.")
    return 0
