#!/usr/bin/env python3
"""Builder bridge — lets the assistant propose code changes through git.

Three things were asked for and are one system, because they all reduce to
"the assistant writes code, a human decides whether it ships":

- building services on demand,
- extending its own source, by pull request and never self-merge,
- a version-control trail for whatever it produces.

Git is the trail. Every proposal is a branch with a commit and a message, so
nothing this produces is untracked and reviewing it is `git diff`.

## Why this is the most dangerous service here, and what stops it

Every other bridge holds a credential to somebody else's API. This one can
write the source of the system that constrains it. If the assistant could
propose an edit to `policies/approval-policy.yaml`, it could propose granting
itself everything and then need only one distracted approval to get it. That is
not a hypothetical failure of review; it is the review being handed a diff
whose significance is easy to miss.

So the containment is not "the operator will read it carefully":

**Protected paths are refused outright.** The assistant may not propose a
change to the policy, either gate, the operator CLI, or CI — no matter what it
says the change is for. This is `modify_upstream_agent_source` and
`disable_approval_gates`, both `always_denied`, enforced as a path check rather
than left to a tier lookup that a cleverly-named tool might sidestep.

**It works in its own clone.** `/repo` is a clone, not the operator's working
tree, so nothing here can dirty an in-progress edit or a stash.

**It pushes branches and nothing else.** No merge, no deploy, no force, no tag,
no branch deletion. `merge_own_pr` is `always_denied` and a service that could
merge would route around it.

**Nothing it writes runs here.** `run_repo_checks` runs the repo's validators
against the *checked-out branch*, in this container, with no network — it is a
lint, not a deployment. Generated code only ever executes after an operator
merges and deploys it.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from bridge_base import BridgeError, BridgeHandler, resolve_limit, serve

REPO = Path(os.environ.get("BUILDER_REPO", "/repo"))
GIT_TIMEOUT = int(os.environ.get("BUILDER_GIT_TIMEOUT", "120"))
CHECK_TIMEOUT = int(os.environ.get("BUILDER_CHECK_TIMEOUT", "600"))
BRANCH_PREFIX = os.environ.get("BUILDER_BRANCH_PREFIX", "proposal/")
MAX_FILE_BYTES = int(os.environ.get("BUILDER_MAX_FILE_BYTES", str(256 * 1024)))
MAX_FILES_PER_PROPOSAL = int(os.environ.get("BUILDER_MAX_FILES", "25"))

# --- what the assistant may never propose a change to -------------------------
#
# Each of these either constrains the assistant or is the operator's own tooling.
# Matched as path prefixes against the repo-relative path, after normalisation,
# so `policies/../policies/x` and `./policies/x` both resolve into the check.
#
# Deliberately a denylist of *specific, named* things rather than an allowlist
# of writable areas: the whole point is that the assistant can propose changes
# anywhere it might usefully work, including its own MCP servers and bridges.
# An allowlist would have to be widened constantly and would be widened
# carelessly.
PROTECTED = (
    "policies/",                                  # the tiers that gate it
    "services/templates/mcp/policy_gate.py",      # the MCP-side gate
    "services/templates/bridge/app/bridge_base.py",  # the authoritative gate
    "services/compose/builder-bridge/",           # itself
    "services/compose/builder-mcp/",              # its own front door
    "cli/",                                       # grant, deploy, doctor, backup
    ".github/",                                   # CI
    ".git/",                                      # the trail itself
)

SAFE_BRANCH = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,80}$")


def git(*args: str, timeout: int = GIT_TIMEOUT) -> str:
    """Run a git command in the clone and return stdout.

    Never takes a shell string: every argument is passed through a list, so a
    branch name or commit message cannot become another command.
    """
    result = subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, text=True, timeout=timeout,
        stdin=subprocess.DEVNULL,
        # A commit needs an identity, and the assistant's commits should be
        # visibly its own in `git log` rather than impersonating the operator.
        env={**os.environ,
             "GIT_AUTHOR_NAME": "agentbox-assistant",
             "GIT_AUTHOR_EMAIL": "assistant@agentbox.local",
             "GIT_COMMITTER_NAME": "agentbox-assistant",
             "GIT_COMMITTER_EMAIL": "assistant@agentbox.local",
             "GIT_TERMINAL_PROMPT": "0"})
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:400]
        raise BridgeError(500, f"git {args[0]} failed: {detail}")
    return result.stdout


def repo_relative(path: str) -> str:
    """Normalise a caller-supplied path and refuse anything outside the repo."""
    if not path or path.startswith("/") or "\0" in path:
        raise BridgeError(400, f"path must be relative to the repo root: {path!r}")
    normalised = os.path.normpath(path).replace(os.sep, "/")
    if normalised.startswith("../") or normalised == "..":
        raise BridgeError(400, f"path escapes the repo: {path!r}")
    return normalised


def refuse_if_protected(path: str) -> None:
    normalised = repo_relative(path)
    for guarded in PROTECTED:
        if normalised == guarded.rstrip("/") or normalised.startswith(guarded):
            raise BridgeError(
                403,
                f"'{normalised}' is protected and cannot be changed by proposal. "
                f"It governs or tools the assistant, so editing it is "
                f"modify_upstream_agent_source / disable_approval_gates, both "
                f"always_denied. Ask the operator to make this change directly.")


# --- reading ------------------------------------------------------------------


def query_of(handler) -> dict:
    import urllib.parse
    return urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)


def first(query, key, default=""):
    value = query.get(key, [default])
    return value[0] if value else default


def list_files(handler, body):
    """List tracked files, optionally under a subdirectory."""
    query = query_of(handler)
    prefix = first(query, "prefix", "")
    limit = resolve_limit(first(query, "limit", ""), default=200, maximum=2000)
    args = ["ls-files"]
    if prefix:
        args.append(repo_relative(prefix))
    files = [line for line in git(*args).splitlines() if line]
    return 200, {"files": files[:limit], "total": len(files),
                 "protected": list(PROTECTED)}


def read_file(handler, body):
    query = query_of(handler)
    path = repo_relative(first(query, "path"))
    target = REPO / path
    if not target.is_file():
        raise BridgeError(404, f"no such file: {path}")
    if target.stat().st_size > MAX_FILE_BYTES:
        raise BridgeError(413, f"{path} is larger than {MAX_FILE_BYTES} bytes")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise BridgeError(415, f"{path} is not text")
    # Readable but not writable — say so here rather than letting the assistant
    # draft a change and only discover the refusal on submit.
    protected = any(path == g.rstrip("/") or path.startswith(g) for g in PROTECTED)
    return 200, {"path": path, "content": content, "writable": not protected}


def list_proposals(handler, body):
    """Branches this service has produced, newest first."""
    out = git("for-each-ref", "--sort=-committerdate",
              "--format=%(refname:short)%09%(committerdate:iso8601)%09%(contents:subject)",
              f"refs/heads/{BRANCH_PREFIX}*")
    proposals = []
    for line in out.splitlines():
        if not line.strip():
            continue
        name, _, rest = line.partition("\t")
        date, _, subject = rest.partition("\t")
        proposals.append({"branch": name, "committed": date, "subject": subject})
    return 200, {"proposals": proposals, "total": len(proposals)}


# --- checking -----------------------------------------------------------------


def run_checks(handler, body):
    """Run the repo's own validator against the working tree.

    Its own validator, not an arbitrary command: `run_validators_and_tests` is
    `allowed`, and it stays safe to be `allowed` only because what runs is
    fixed. A generic "run this" endpoint under the same capability would be
    shell access with extra steps.
    """
    result = subprocess.run(
        ["python", "cli/agentbox", "validate"],
        cwd=str(REPO), capture_output=True, text=True,
        timeout=CHECK_TIMEOUT, stdin=subprocess.DEVNULL)
    output = (result.stdout + result.stderr).strip()
    return 200, {
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        # Tail, not head: validate prints failures last.
        "output": output[-8000:],
    }


# --- proposing ----------------------------------------------------------------


def propose(handler, body):
    """Write files onto a fresh branch and push it for review."""
    body = body or {}
    branch_name = str(body.get("branch") or "").strip()
    message = str(body.get("message") or "").strip()
    rationale = str(body.get("rationale") or "").strip()
    files = body.get("files")

    if not SAFE_BRANCH.match(branch_name):
        raise BridgeError(400, "branch must be lowercase letters, digits, "
                               "dot, dash, underscore or slash")
    if not message:
        raise BridgeError(400, "message is required — the commit message is the "
                               "explanation the operator reads first")
    if not isinstance(files, list) or not files:
        raise BridgeError(400, "files must be a non-empty list of "
                               "{path, content}")
    if len(files) > MAX_FILES_PER_PROPOSAL:
        raise BridgeError(400, f"at most {MAX_FILES_PER_PROPOSAL} files per "
                               f"proposal; split the change")

    prepared = []
    for entry in files:
        if not isinstance(entry, dict):
            raise BridgeError(400, "each file must be {path, content}")
        path = repo_relative(str(entry.get("path") or ""))
        refuse_if_protected(path)
        content = entry.get("content")
        if not isinstance(content, str):
            raise BridgeError(400, f"content for {path} must be a string")
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise BridgeError(413, f"{path} exceeds {MAX_FILE_BYTES} bytes")
        prepared.append((path, content))

    branch = branch_name if branch_name.startswith(BRANCH_PREFIX) else \
        f"{BRANCH_PREFIX}{branch_name}"

    # Start from a clean, current base every time. Without the reset a failed
    # earlier proposal would leave changes lying around and silently ride along
    # inside the next one.
    git("fetch", "origin", "--prune")
    git("reset", "--hard", "HEAD")
    git("clean", "-fd")
    default = default_branch()
    git("checkout", "-B", branch, f"origin/{default}")

    for path, content in prepared:
        target = REPO / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        git("add", "--", path)

    if not git("status", "--porcelain").strip():
        git("checkout", default)
        raise BridgeError(409, "the proposed files are identical to "
                               f"{default}; nothing to propose")

    full_message = message
    if rationale:
        full_message += f"\n\n{rationale}"
    full_message += ("\n\nProposed by the agentbox assistant. Not merged, not "
                     "deployed — review with `git diff " + default + "..." +
                     branch + "`.")
    git("commit", "-m", full_message)

    stat = git("diff", "--stat", f"origin/{default}...{branch}")
    head = git("rev-parse", "--short", "HEAD").strip()
    git("checkout", default)

    # Deliberately NOT pushed. The branch stays in this clone and the operator
    # fetches it, which means this container never needs write access to the
    # operator's repository — the alternative was granting a service the
    # assistant drives the ability to write refs into the real .git directory,
    # to save the operator one fetch. Pull, not push, is the right direction
    # for anything proposing changes to itself.
    return 201, {
        "branch": branch,
        "commit": head,
        "files": [path for path, _ in prepared],
        "diffstat": stat.strip(),
        "review": f"cli/agentbox proposals show {branch.split('/', 1)[-1]}",
        "note": "Waiting for review in the builder clone. Nothing is merged or "
                "deployed; both are operator actions and neither is available "
                "to this service.",
    }


def default_branch() -> str:
    try:
        ref = git("symbolic-ref", "refs/remotes/origin/HEAD").strip()
        return ref.rsplit("/", 1)[-1] or "main"
    except BridgeError:
        return "main"


def get_schema(handler, body):
    return 200, {
        "routes": ["GET /v1/files", "GET /v1/file", "GET /v1/proposals",
                   "POST /v1/checks", "POST /v1/proposals"],
        "protected_paths": list(PROTECTED),
        "branch_prefix": BRANCH_PREFIX,
        "cannot": ["merge", "deploy", "delete branches", "force push",
                   "edit protected paths"],
    }


class BuilderBridge(BridgeHandler):
    server_version = "builder-bridge/1.0"
    bridge_token = os.environ.get("BUILDER_BRIDGE_TOKEN", "")
    routes = {
        ("GET", "/schema"): get_schema,
        ("GET", "/v1/files"): list_files,
        ("GET", "/v1/file"): read_file,
        ("GET", "/v1/proposals"): list_proposals,
        ("POST", "/v1/checks"): run_checks,
        ("POST", "/v1/proposals"): propose,
    }

    def capability_for(self, method: str, path: str,
                       body: dict[str, Any] | None) -> str | None:
        if path.startswith("/v1/file"):
            return "read_repo_files"
        if path.startswith("/v1/checks"):
            return "run_validators_and_tests"
        if path.startswith("/v1/proposals"):
            return "create_branches" if method == "POST" else "read_repo_files"
        return None

    def upstream_status(self) -> dict[str, Any]:
        """Readiness is: is there a git repo here, and can we reach its origin?

        A clone that cannot fetch produces proposals off a stale base, which
        looks fine until the operator tries to merge one.
        """
        if not (REPO / ".git").exists():
            return {"ok": False, "upstream": {"repo": str(REPO),
                                              "error": "not a git repository"}}
        try:
            git("fetch", "origin", "--dry-run", timeout=20)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "upstream": {"repo": str(REPO),
                                              "error": str(exc)[:200]}}
        return {"ok": True, "upstream": {"repo": str(REPO),
                                         "default_branch": default_branch()}}


if __name__ == "__main__":
    serve(BuilderBridge)
