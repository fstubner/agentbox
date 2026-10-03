"""Tests for the builder, through which the assistant proposes code changes.

It can write the source of the system that constrains it, which makes it the
most dangerous service here. A proposal touching the policy, either gate, the
operator CLI or CI is refused, so the operator is never asked to approve that
diff.

Most of these try to get around that check. If one passed, an assistant could
grant itself more authority with one careless approval.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "services" / "templates" / "bridge" / "app"))
sys.path.insert(0, str(REPO / "services" / "templates" / "mcp"))


def load_builder(repo_path=None):
    import os
    if repo_path:
        os.environ["BUILDER_REPO"] = str(repo_path)
    spec = importlib.util.spec_from_file_location(
        "builder_bridge_under_test",
        REPO / "services" / "compose" / "builder-bridge" / "app" / "bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def builder():
    return load_builder()


# --- the protected-path check ------------------------------------------------


PROTECTED_ATTEMPTS = [
    "policies/approval-policy.yaml",
    "policies/approval-policy.md",
    "services/templates/mcp/policy_gate.py",
    "services/templates/bridge/app/bridge_base.py",
    "cli/agentbox",
    "cli/agentbox-approvals",
    ".github/workflows/ci.yml",
    "services/compose/builder-bridge/app/bridge.py",
]


@pytest.mark.parametrize("path", PROTECTED_ATTEMPTS)
def test_protected_paths_are_refused(builder, path):
    with pytest.raises(builder.BridgeError) as exc:
        builder.refuse_if_protected(path)
    assert exc.value.status == 403


TRAVERSAL_ATTEMPTS = [
    "policies/../policies/approval-policy.yaml",
    "./policies/approval-policy.yaml",
    "services/../policies/approval-policy.yaml",
    "policies//approval-policy.yaml",
    "cli/../cli/agentbox",
]


@pytest.mark.parametrize("path", TRAVERSAL_ATTEMPTS)
def test_traversal_does_not_reach_a_protected_path(builder, path):
    """Normalisation happens before the prefix check, or `policies/../policies/x`
    slips past a naive startswith."""
    with pytest.raises(builder.BridgeError) as exc:
        builder.refuse_if_protected(path)
    assert exc.value.status == 403


ESCAPE_ATTEMPTS = [
    "../outside.txt",
    "../../etc/passwd",
    "/etc/passwd",
    "..",
]


@pytest.mark.parametrize("path", ESCAPE_ATTEMPTS)
def test_paths_outside_the_repo_are_refused(builder, path):
    with pytest.raises(builder.BridgeError):
        builder.repo_relative(path)


def test_a_null_byte_is_refused(builder):
    with pytest.raises(builder.BridgeError):
        builder.repo_relative("services/foo\0.py")


def test_ordinary_paths_are_allowed(builder):
    for path in ("services/compose/todoist-bridge/app/bridge.py",
                 "docs/architecture.md",
                 "tests/test_todoist.py",
                 "skills/new-skill/SKILL.md"):
        builder.refuse_if_protected(path)  # must not raise


def test_a_service_named_to_look_protected_is_still_allowed(builder):
    """The check matches path prefixes, not the word 'policy', so a bridge
    for an insurance API can still be built."""
    builder.refuse_if_protected("services/compose/policy-quotes-bridge/app/bridge.py")


def test_the_protected_list_covers_both_gates(builder):
    """Named explicitly. These two files hold all runtime enforcement, and a
    proposal that edits either one disables it without any visible error."""
    joined = " ".join(builder.PROTECTED)
    assert "policy_gate.py" in joined
    assert "bridge_base.py" in joined
    assert "policies/" in joined
    assert "cli/" in joined


# --- git safety ---------------------------------------------------------------


def test_branch_names_are_constrained(builder):
    assert builder.SAFE_BRANCH.match("add-todoist-bridge")
    assert builder.SAFE_BRANCH.match("fix/calendar-lean-view")
    # Anything that could become a flag or escape an argument position.
    for bad in ("--force", "-D main", "a;rm -rf /", "a b", "A/B", "", "a" * 200,
                "..", "refs/heads/main\nmore"):
        assert not builder.SAFE_BRANCH.match(bad), bad


def test_git_is_never_invoked_through_a_shell():
    """A commit message is text written by a model that may have just read an
    email, so it must never reach a shell."""
    src = (REPO / "services" / "compose" / "builder-bridge" / "app"
           / "bridge.py").read_text()
    assert "shell=True" not in src
    assert "os.system" not in src


def test_the_bridge_never_pushes():
    """Proposals stay in the clone and the operator fetches them, so this
    container never needs write access to the operator's repository. Pushing
    would let a service the assistant drives write refs into the real .git
    directory, only to save the operator one fetch."""
    import re
    src = (REPO / "services" / "compose" / "builder-bridge" / "app"
           / "bridge.py").read_text()
    assert "push" not in set(re.findall(r'\bgit\(\s*"([a-z-]+)"', src))


def test_the_bridge_cannot_merge_or_deploy():
    """merge_own_pr is always_denied. A builder that could merge would get
    around that, and one that could deploy would make review optional.

    Checks the git subcommands the code runs, not the source text, which also
    contains the schema string listing what the service cannot do.
    """
    import re
    src = (REPO / "services" / "compose" / "builder-bridge" / "app"
           / "bridge.py").read_text()
    invoked = set(re.findall(r'\bgit\(\s*"([a-z-]+)"', src))
    permitted = {"fetch", "reset", "clean", "checkout", "add", "status",
                 "commit", "diff", "rev-parse", "ls-files",
                 "for-each-ref", "symbolic-ref"}
    assert invoked <= permitted, f"unexpected git subcommands: {invoked - permitted}"
    for dangerous in ("merge", "rebase", "cherry-pick", "tag", "reflog"):
        assert dangerous not in invoked
    # And nothing here deploys.
    assert "docker" not in src.lower().replace("dockerfile", "")


def test_checks_run_a_fixed_command_not_a_supplied_one():
    """`run_validators_and_tests` is `allowed`, which is only safe while what
    runs is fixed. A generic 'run this' endpoint under that tier would give
    the assistant shell access."""
    src = (REPO / "services" / "compose" / "builder-bridge" / "app"
           / "bridge.py").read_text()
    block = src.split("def run_checks", 1)[1].split("\ndef ", 1)[0]
    assert '"cli/agentbox", "validate"' in block
    assert "body.get" not in block


# --- policy wiring -------------------------------------------------------------


def test_every_builder_tool_is_mapped():
    import policy_gate as pg
    mapping = pg.load_tool_map(REPO / "policies" / "approval-policy.yaml")
    expected = {
        "list_repo_files": "read_repo_files",
        "read_repo_file": "read_repo_files",
        "list_proposals": "read_repo_files",
        "run_repo_checks": "run_validators_and_tests",
        "propose_change": "create_branches",
    }
    for tool, capability in expected.items():
        assert mapping.get(tool) == capability, tool


def test_merging_stays_denied():
    import policy_gate as pg
    policy = REPO / "policies" / "approval-policy.yaml"
    tiers = pg.load_tiers(policy)
    assert "merge_own_pr" in tiers["always_denied"]
    assert "modify_upstream_agent_source" in tiers["always_denied"]
    # No tool maps to either, so there is no route to them.
    mapping = pg.load_tool_map(policy)
    assert "merge_own_pr" not in mapping.values()
    assert "modify_upstream_agent_source" not in mapping.values()


def test_the_bridge_declares_its_capabilities():
    src = (REPO / "services" / "compose" / "builder-bridge" / "app"
           / "bridge.py").read_text()
    assert "def capability_for" in src
    for capability in ("read_repo_files", "run_validators_and_tests",
                       "create_branches"):
        assert capability in src


def test_tool_descriptions_say_proposals_do_not_ship():
    """A model that believes it has shipped will tell the operator the work is
    done, and the operator may trust that."""
    src = (REPO / "services" / "compose" / "agentbox-mcp" / "app"
           / "integrations" / "builder.py").read_text()
    assert "does NOT merge" in src and "does NOT deploy" in src


# --- end to end against a real repository --------------------------------------


@pytest.fixture
def git_repo(tmp_path):
    """An origin plus a clone, so push behaviour is exercised for real."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    clone = tmp_path / "clone"
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}

    def run(*args, cwd):
        subprocess.run(args, cwd=str(cwd), check=True,
                       capture_output=True, env=env)

    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   check=True, capture_output=True, env=env)
    subprocess.run(["git", "clone", str(origin), str(work)],
                   check=True, capture_output=True, env=env)
    (work / "docs").mkdir()
    (work / "docs" / "readme.md").write_text("hello\n")
    (work / "policies").mkdir()
    (work / "policies" / "approval-policy.yaml").write_text("tiers: {}\n")
    run("git", "add", "-A", cwd=work)
    run("git", "commit", "-m", "initial", cwd=work)
    run("git", "push", "origin", "main", cwd=work)
    subprocess.run(["git", "clone", str(origin), str(clone)],
                   check=True, capture_output=True, env=env)
    subprocess.run(["git", "remote", "set-head", "origin", "main"],
                   cwd=str(clone), check=True, capture_output=True, env=env)
    return clone


class FakeHandler:
    def __init__(self, query: str = "") -> None:
        self.path = f"/v1/x{query}"


def test_a_proposal_becomes_a_branch_not_a_change_to_main(git_repo):
    builder = load_builder(git_repo)
    status, payload = builder.propose(FakeHandler(), {
        "branch": "add-a-doc", "message": "docs: add a note",
        "files": [{"path": "docs/note.md", "content": "a note\n"}]})
    assert status == 201
    assert payload["branch"] == "proposal/add-a-doc"

    # main is untouched.
    on_main = subprocess.run(["git", "show", "main:docs/note.md"],
                             cwd=str(git_repo), capture_output=True, text=True)
    assert on_main.returncode != 0

    # and the branch has it.
    on_branch = subprocess.run(
        ["git", "show", "proposal/add-a-doc:docs/note.md"],
        cwd=str(git_repo), capture_output=True, text=True)
    assert on_branch.stdout == "a note\n"


def test_a_proposal_touching_the_policy_is_refused_before_any_git_runs(git_repo):
    builder = load_builder(git_repo)
    with pytest.raises(builder.BridgeError) as exc:
        builder.propose(FakeHandler(), {
            "branch": "sneaky", "message": "chore: tidy",
            "files": [{"path": "docs/fine.md", "content": "ok\n"},
                      {"path": "policies/approval-policy.yaml",
                       "content": "tiers:\n  allowed:\n    - everything\n"}]})
    assert exc.value.status == 403
    # No branch was created: the whole proposal is validated before anything is
    # written, so a protected file cannot ride along with innocuous ones.
    branches = subprocess.run(["git", "branch", "-a"], cwd=str(git_repo),
                              capture_output=True, text=True).stdout
    assert "sneaky" not in branches


def test_an_identical_proposal_is_refused_rather_than_committing_nothing(git_repo):
    builder = load_builder(git_repo)
    with pytest.raises(builder.BridgeError) as exc:
        builder.propose(FakeHandler(), {
            "branch": "no-op", "message": "docs: no change",
            "files": [{"path": "docs/readme.md", "content": "hello\n"}]})
    assert exc.value.status == 409


def test_a_second_proposal_does_not_inherit_the_first(git_repo):
    """Each proposal starts from the default branch. Without the reset, a
    previous proposal's files ride along inside the next one."""
    builder = load_builder(git_repo)
    builder.propose(FakeHandler(), {
        "branch": "first", "message": "docs: first",
        "files": [{"path": "docs/first.md", "content": "1\n"}]})
    builder.propose(FakeHandler(), {
        "branch": "second", "message": "docs: second",
        "files": [{"path": "docs/second.md", "content": "2\n"}]})
    files = subprocess.run(["git", "ls-tree", "-r", "--name-only",
                            "proposal/second"],
                           cwd=str(git_repo), capture_output=True,
                           text=True).stdout
    assert "docs/second.md" in files
    assert "docs/first.md" not in files


def test_the_commit_is_attributed_to_the_assistant(git_repo):
    """`git log` should show who wrote it. A proposal committed as the operator
    cannot be told apart from the operator's own work."""
    builder = load_builder(git_repo)
    builder.propose(FakeHandler(), {
        "branch": "attributed", "message": "docs: who wrote this",
        "files": [{"path": "docs/x.md", "content": "x\n"}]})
    author = subprocess.run(["git", "log", "-1", "--format=%an",
                             "proposal/attributed"],
                            cwd=str(git_repo), capture_output=True,
                            text=True).stdout.strip()
    assert author == "agentbox-assistant"


def test_reading_a_protected_file_is_allowed_but_marked_unwritable(git_repo):
    """It may read the policy, since knowing what it may do is not changing
    it, but the response must say the file is protected, so it does not draft
    a change that will be refused."""
    builder = load_builder(git_repo)
    _, payload = builder.read_file(
        FakeHandler("?path=policies/approval-policy.yaml"), None)
    assert payload["writable"] is False
    _, ordinary = builder.read_file(FakeHandler("?path=docs/readme.md"), None)
    assert ordinary["writable"] is True


def test_the_code_that_enforces_the_policy_is_protected_too():
    """The code that enforces the policy is protected as well as the policy.

    That includes mcp_base.py, which holds the only call to policy_gate.check
    and the fail-closed auth, the memory review gate and the rule-approval
    fingerprints.
    """
    import importlib.machinery
    import importlib.util
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "services/templates/bridge/app"))
    loader = importlib.machinery.SourceFileLoader(
        "builder_protected", str(repo / "services/compose/builder-bridge/app/bridge.py"))
    spec = importlib.util.spec_from_loader("builder_protected", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["builder_protected"] = module
    spec.loader.exec_module(module)

    must_refuse = [
        "services/templates/mcp/mcp_base.py",
        "services/compose/memory-bridge/app/bridge.py",
        "services/compose/agentbox-mcp/app/evaluator.py",
        "router/agentbox_router.py",
        # previously protected paths, so a reordering cannot lose them
        "policies/approval-policy.yaml",
        "services/templates/mcp/policy_gate.py",
        "cli/agentbox",
    ]
    for path in must_refuse:
        try:
            module.refuse_if_protected(path)
        except Exception:
            continue
        raise AssertionError(f"{path} is not protected")
