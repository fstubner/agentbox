"""Tests that grep source must not be fooled by comments.

`assert "x" in source` passes when a comment mentions x, even if the code is
wrong. `assert "x" not in source` fails when a comment explains why x was
removed. Both test prose instead of code.

Some properties really are structural, such as "this file must not import
that", and reading the source is the honest way to check them. So this does not
ban source assertions. It bans asserting on raw text that still contains
comments.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import strip_comments

TESTS = Path(__file__).resolve().parent
# Reads raw text and asserts against it in the same file, without going
# through the shared helper.
RAW_READ = re.compile(r"^\s*(\w+)\s*=\s*.*read_text\(", re.M)


def _files():
    return sorted(p for p in TESTS.glob("test_*.py") if p.name != Path(__file__).name)


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_source_assertions_go_through_the_comment_stripper(path):
    """A test asserting on file text must strip comments first.

    Use `code_of(...)`, `script_code(...)` or `strip_comments(...)`. Reading
    data files with `read_text()` is fine. What this catches is asserting a
    code construct against text that still contains prose.
    """
    text = path.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines()
                      if "asserts-on-prose" not in line)
    code = strip_comments(code)
    # A test may assert on prose on purpose, for example that a module
    # documents a limitation. Such reads are marked `# asserts-on-prose`, so a
    # reviewer can see the exemption.
    names = set(RAW_READ.findall(code))
    if not names:
        return
    offenders = []
    for name in names:
        # An assertion about this variable that never passes through a stripper.
        asserts = re.findall(rf"^\s*assert .*\b{name}\b.*$", code, re.M)
        stripped = (f"strip_comments({name})" in code
                    or "code_of(" in code
                    or "script_code(" in code
                    or f"_code_only({name})" in code)
        if asserts and not stripped:
            offenders.append((name, len(asserts)))
    assert not offenders, (
        f"{path.name} asserts on raw file text: {offenders}. "
        f"Use code_of(path) or strip_comments(text) from tests/conftest.py — "
        f"a comment must not be able to satisfy or break a test about code.")
