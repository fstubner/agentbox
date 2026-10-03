"""Telling a memory from feedback."""
from __future__ import annotations

import re
from typing import Any

# --- memory and feedback ---------------------------------------------------
#
# Two kinds of proposal arrive through the same route. "Sam is allergic to
# peanuts" is a fact and belongs in memory. "Stop asking me to confirm every
# calendar read" is a complaint about behaviour. Storing that as a memory
# patches around the problem and spends a line of context every session, so it
# goes to a backlog to be fixed in the skill, tool description or prompt
# instead.
#
# The classification is a heuristic and may be wrong. The reviewer sees which
# way it went and why, and can flip it.

KIND_MEMORY = "memory"
KIND_FEEDBACK = "feedback"

# Phrases about the assistant's conduct rather than the world. Second person
# plus a directive is the main signal, since a fact about a person rarely
# addresses the reader.
FEEDBACK_MARKERS = (
    "you should", "you shouldn't", "you should not", "you must", "you need to",
    "you keep", "you always", "you never", "you tend to", "you often",
    "don't ask", "do not ask", "stop asking", "stop doing", "stop telling",
    "instead of asking", "rather than asking", "prefer that you",
    "i'd prefer you", "i would prefer you", "please don't", "please do not",
    "remember to ask", "make sure you", "be more", "be less",
    "too verbose", "too long", "too many questions", "annoying",
    "asked you", "told you", "keeps happening", "every time i ask",
    "when i ask you", "you got it wrong", "you were wrong", "that was wrong",
)


# Words that only appear when the assistant is describing its own machinery,
# such as a note that a tool keeps failing. In practice that is the most common
# kind of feedback, and the fix is to repair the tool rather than remember that
# it is broken.
SELF_REPORT_MARKERS = (
    "upstream_rejected", "approval_required", "always_denied",
    "was refused", "were refused", "refused ", "% success", "0/",
    "calls returned", "returned upstream", "this tool", "the tool",
    "tool description", "requires ", "required argument",
    "do not retry", "do not silently", "do not propose", "don't propose",
    "before proposing", "before calling", "before i ", "always call",
    "always run", "when in doubt", "my memory proposals", "my calls",
    "i need a grant", "ask the operator to grant",
)

# A tool name, snake_case with at least one underscore. Household facts do not
# mention find_or_create_task, so a proposal that does is about the machine.
TOOL_NAME = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def classify_kind(statement: str) -> tuple[str, str]:
    """Guess whether this is a fact to remember or feedback to act on.

    Returns (kind, reason). The reason is shown to the reviewer so the guess can
    be corrected with confidence. The signals, most certain first, are someone
    addressing the assistant's conduct, the assistant reporting on its own
    machinery, and a bare directive.
    """
    text = " " + statement.lower().strip() + " "
    for marker in FEEDBACK_MARKERS:
        if marker in text:
            return KIND_FEEDBACK, f"sounds like feedback about behaviour (“{marker.strip()}”)"
    for marker in SELF_REPORT_MARKERS:
        if marker in text:
            return KIND_FEEDBACK, (f"describes how a tool behaved "
                                   f"(“{marker.strip()}”), worth fixing, not remembering")
    match = TOOL_NAME.search(statement.lower())
    if match:
        return KIND_FEEDBACK, (f"names a tool (“{match.group(0)}”), so it is "
                               f"probably about the system rather than the household")
    # A bare instruction to the assistant, such as "always confirm before…" or
    # "never read my email out loud".
    first = text.strip().split(" ")[0] if text.strip() else ""
    if first in ("always", "never", "stop", "don't", "dont", "avoid"):
        return KIND_FEEDBACK, f"starts with a directive (“{first}”)"
    return KIND_MEMORY, ""


def resolve_kind(body: dict[str, Any], statement: str) -> tuple[str, str, str]:
    """(kind, reason, source). An explicit kind always beats the guess."""
    requested = str(body.get("kind") or "").strip().lower()
    if requested in (KIND_MEMORY, KIND_FEEDBACK):
        return requested, str(body.get("kind_reason") or ""), "explicit"
    kind, reason = classify_kind(statement)
    return kind, reason, "auto"
