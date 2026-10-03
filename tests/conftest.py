"""Shared helpers for the test suite.

Tests that assert on source must not be fooled by comments.
`assert "x" not in source` fails when a comment explains why x was removed, and
`assert "x" in source` passes when a comment mentions x even if the code is
wrong. `code_of()` strips comments and docstrings, so an assertion is about
code. Prefer testing behaviour, and use `code_of(...)` where the property is
structural, such as "this file must not import that".
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# The CLI is split into sibling modules under cli/. Tests import them by
# name, the same way cli/agentbox does.
CLI_DIR = REPO_ROOT / "cli"
if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))

# Bridges split into sibling modules with names unique to each bridge
# (memory_store, google_drive, ...), imported by name from bridge.py the same
# way the container's /app directory makes them importable.
for _app in sorted((REPO_ROOT / "services" / "compose").glob("*/app")):
    if str(_app) not in sys.path:
        sys.path.append(str(_app))


def strip_comments(text: str) -> str:
    """Remove `#` comments without touching `#` inside string literals."""
    out = []
    for line in text.splitlines():
        quote = None
        cut = len(line)
        index = 0
        while index < len(line):
            char = line[index]
            if quote:
                if char == "\\":
                    index += 2
                    continue
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == "#":
                cut = index
                break
            index += 1
        out.append(line[:cut])
    return "\n".join(out)


def code_of(path: str | Path) -> str:
    """A file's executable text, with no comments or docstrings.

    Falls back to stripping comments only for files that do not parse as
    Python. The extensionless CLI scripts do parse.
    """
    full = Path(path)
    if not full.is_absolute():
        full = REPO_ROOT / full
    raw = full.read_text(encoding="utf-8")
    without_comments = strip_comments(raw)
    try:
        tree = ast.parse(without_comments)
    except SyntaxError:
        return without_comments
    # Blank out docstrings, which are as misleading as comments for this.
    lines = without_comments.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            for n in range(first.lineno - 1, (first.end_lineno or first.lineno)):
                if n < len(lines):
                    lines[n] = ""
    return "\n".join(lines)


@pytest.fixture
def code():
    """Usage: `code("cli/agentbox")` -> executable text only."""
    return code_of


def script_code(name: str) -> str:
    """A CLI script's executable text, including the modules it was split into.

    `script_code("agentbox-portal")` reads cli/agentbox-portal and every
    cli/agentbox_portal_*.py, because an assertion about the program has to see
    all of its files.
    """
    stem = name.replace("-", "_")
    files = [CLI_DIR / name, *sorted(CLI_DIR.glob(f"{stem}_*.py"))]
    return "\n".join(code_of(f) for f in files)


def portal_code() -> str:
    return script_code("agentbox-portal")


def _namespaces_holding(module, name):
    """`module`'s namespace and those of the modules its functions came from,
    wherever `name` is defined."""
    spaces = {id(module.__dict__): module.__dict__}
    for obj in list(vars(module).values()):
        namespace = getattr(obj, "__globals__", None)
        if isinstance(namespace, dict):
            spaces[id(namespace)] = namespace
    hits = [ns for ns in spaces.values() if name in ns]
    assert hits, f"{name} is not defined anywhere {module.__name__} reaches"
    return hits


def patch_everywhere(monkeypatch, module, name, value):
    """Patch `name` in a split module and in every module its functions came from.

    A module split into siblings re-exports their functions, and each looks
    names up in its own module. Patching only the re-exporting module would
    miss them, so this patches every namespace that holds the name.
    """
    for namespace in _namespaces_holding(module, name):
        monkeypatch.setitem(namespace, name, value)


def set_everywhere(module, name, value):
    """Like patch_everywhere, for a module loaded fresh for one test."""
    for namespace in _namespaces_holding(module, name):
        namespace[name] = value


def drop_modules(prefix: str) -> None:
    """Forget cached modules starting with `prefix`, so the next load reads the
    environment afresh. Split bridges read some settings when they load."""
    for name in [n for n in sys.modules if n.startswith(prefix)]:
        sys.modules.pop(name)


# Split modules that read settings from the environment when they load. Each
# test starts without them cached, so a test that sets the environment and then
# loads a bridge or script gets modules that read its settings, not a previous
# test's.
FRESH_PER_TEST = ("google_", "ha_", "agentbox_approvals_", "agentbox_invite_")


@pytest.fixture(autouse=True)
def _fresh_split_modules():
    for prefix in FRESH_PER_TEST:
        drop_modules(prefix)
