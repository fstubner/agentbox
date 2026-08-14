"""Shared helpers for tests that assert on source files.

A test that greps source has bitten this suite three times, always the same
way: the assertion matched a *comment* rather than code.

- `assert "x" not in source` fails when a comment explains why x was removed —
  the test breaks on the change that fixed the thing it guards. That happened
  to the `ignore_errors` check the moment the backup leak was fixed.
- `assert "x" in source` is worse: a comment mentioning x makes the test pass
  while the code is wrong, which is a test that cannot fail for the right
  reason.

`code_of()` strips comments and docstrings, so an assertion is about code.
Prefer exercising behaviour where you can; where the property really is
structural — "this file must not import that", "this call must come before
that one" — assert on `code_of(...)` rather than raw text.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


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
    """A file's executable text: no comments, no docstrings.

    Falls back to comment-stripping alone for files that are not importable
    Python — cli/agentbox is extensionless but parses, so it works there too.
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
