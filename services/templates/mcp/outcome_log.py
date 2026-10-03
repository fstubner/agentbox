"""What happened when the assistant used a tool.

The bridges log requests, but they sit below agentbox-mcp, so they see
`GET /v1/tasks` rather than `list_tasks`, and a call the policy refuses never
reaches them. This records one entry per tool call at the layer where tool
names and policy decisions exist, for self-reflection to read.

## Privacy

Tool arguments carry email bodies, search strings and task titles, and results
carry more.

- Argument names are recorded but values are not, except a short allowlist of
  shape parameters such as `view` and `limit`, which carry no personal data.
- Results are recorded as a size and an outcome, never content.
- Error classes are recorded, never messages, because an upstream message
  often quotes the input that caused it.

This is the same rule as `bridge_base.LOGGED_QUERY_PARAMS`, one layer up.

## Authority

The file shares the writable mount with grant-use and pending records. Writing
here records that something happened and cannot grant anything, so even an
assistant in full control of this file would gain nothing.

It rotates by keeping one previous file, uncompressed, because a log that can
fill the disk is worse than one that loses old lines.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

OUTCOME_FILE = os.environ.get("MCP_OUTCOME_FILE", "")
OUTCOME_MAX_BYTES = int(os.environ.get("MCP_OUTCOME_MAX_BYTES", str(32 * 1024 * 1024)))

# Argument values safe to record: each is a bounded shape parameter, not
# free text. `view` is here because whether the model ever chooses `lean` is
# one of the questions this log exists to answer.
LOGGED_ARG_VALUES = ("view", "limit", "page", "per_page", "expand")

OK = "ok"
ERROR = "error"
DENIED = "denied"          # refused by the policy gate
INVALID = "invalid"        # malformed call: missing required arguments, unknown tool


def _write(record: dict) -> None:
    if not OUTCOME_FILE:
        return
    try:
        path = Path(OUTCOME_FILE)
        if path.exists() and path.stat().st_size > OUTCOME_MAX_BYTES:
            path.replace(path.with_suffix(path.suffix + ".1"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError:
        pass  # recording an outcome must never break the call it describes


def record(service: str, tool: str, outcome: str, *, capability: str = "",
           arguments: dict | None = None, ms: float = 0.0, size: int = 0,
           detail: str = "", identity: str = "") -> None:
    """Append one tool-call outcome.

    `detail` must be a class name or a short fixed reason, never an upstream
    message, which can quote its input.
    """
    arguments = arguments or {}
    entry = {
        "ts": int(time.time()),
        "service": service,
        "tool": tool,
        "outcome": outcome,
        "ms": round(ms * 1000, 1),
    }
    if capability:
        entry["capability"] = capability
    if identity:
        # Who acted. An identity name, never a token; with several people on
        # one gateway, a journal that cannot say whose call it was cannot
        # support a per-person reflection or a per-person tier argument.
        entry["identity"] = identity
    if size:
        entry["bytes"] = size
    if detail:
        entry["detail"] = detail
    # Names always; values only from the allowlist.
    if arguments:
        entry["args"] = sorted(str(k) for k in arguments)
        shape = {k: arguments[k] for k in LOGGED_ARG_VALUES
                 if k in arguments and isinstance(arguments[k], (str, int, bool))}
        if shape:
            entry["shape"] = shape
    _write(entry)


def record_decision(actor: str, action: str, subject: str, detail: str = "") -> None:
    """Record an operator decision: a grant, a rejection or a refusal.

    The only records here that contain a person's judgement, such as approving
    something nine times out of nine.
    """
    entry = {
        "ts": int(time.time()),
        "actor": actor,
        "action": action,
        "subject": subject,
    }
    if detail:
        entry["detail"] = detail
    _write(entry)
