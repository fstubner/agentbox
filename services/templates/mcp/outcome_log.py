"""What happened when the assistant used a tool.

The bridges already log requests, but that log cannot answer the question
reflection needs. It sits below the MCP, so it sees `GET /v1/tasks` and never
learns the tool was `list_tasks`; and a call refused by the policy gate never
reaches a bridge at all, so the most interesting events — the ones where the
assistant wanted something it could not have — are invisible in it.

This is the missing signal. One record per tool call at the layer where tool
names and policy decisions exist, so `cli/agentbox reflect` has something to
read.

**Privacy, which is why this is not simply verbose logging.** Tool arguments
carry email bodies, search strings and task titles; results carry more. So:

- argument *names* are recorded, values are not, except for a small allowlist
  of shape parameters (`view`, `limit`, …) that are enumerated and carry no
  personal data;
- results are recorded as a size and an outcome, never as content;
- error *classes* are recorded, error *messages* are not — an upstream message
  routinely quotes the input that caused it.

The same reasoning as `bridge_base.LOGGED_QUERY_PARAMS`, applied a layer up.

**Authority, which is why the path is safe.** This shares the writable mount
with consumption and pending records. Writing here records that something
happened; it cannot grant permission for anything, so the mount confers no
authority and an assistant that fully controlled this file would gain nothing.

Rotation is crude on purpose — one previous generation, no compression. A
logging path that can fill the disk is worse than one that loses old lines.
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
    message — those quote their input.
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
    """Record an operator decision — a grant, a rejection, a denial.

    These are the highest-value records in the file and the only ones with a
    human judgement in them. "Approved nine times out of nine" is an argument
    for changing a tier; nothing else in the system remembers that.
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
