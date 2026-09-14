"""agentbox_memory — durable memory and feedback management.

Follows the established agentbox modular CLI pattern:
- Dedicated module with clean functional boundaries (< 300 LOC)
- Preserves exact HTTP routes, payload structures, output formatting, and exit codes
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

MEMORY_BRIDGE_URL = os.environ.get("AGENTBOX_MEMORY_BRIDGE", "http://127.0.0.1:3471")


def _rep(report_fn: Callable[[str, str], None] | None, level: str, msg: str) -> None:
    if report_fn:
        report_fn(level, msg)
    else:
        print(f"[{level.lower()}] {msg}")


def memory_request(method: str, path: str, payload: dict | None = None,
                   service_env_fn: Callable[[str, tuple[str, ...]], dict[str, str]] | None = None,
                   report_fn: Callable[[str, str], None] | None = None) -> Any:
    tokens = {}
    if service_env_fn:
        tokens = service_env_fn("memory-bridge", ("MEMORY_BRIDGE_TOKEN", "MEMORY_REVIEW_TOKEN"))
    bridge_token = tokens.get("MEMORY_BRIDGE_TOKEN", "")
    if not bridge_token:
        _rep(report_fn, FAIL, "MEMORY_BRIDGE_TOKEN not found; is memory-bridge.env configured?")
        return None
    headers = {"Authorization": f"Bearer {bridge_token}", "Accept": "application/json"}
    if tokens.get("MEMORY_REVIEW_TOKEN"):
        headers["X-Memory-Review-Token"] = tokens["MEMORY_REVIEW_TOKEN"]
    data = json.dumps(payload).encode() if payload is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(MEMORY_BRIDGE_URL + path, data=data,
                                     method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:300]
        _rep(report_fn, FAIL, f"memory-bridge returned {exc.code}: {detail}")
        return None
    except Exception as exc:  # noqa: BLE001
        _rep(report_fn, FAIL, f"memory-bridge unreachable: {type(exc).__name__}")
        return None


def memory_list(identity: str = "",
                service_env_fn: Callable[[str, tuple[str, ...]], dict[str, str]] | None = None,
                report_fn: Callable[[str, str], None] | None = None) -> int:
    """What the assistant has proposed, across every identity.

    Scope is shown on each line rather than filtered out. A proposal the
    operator cannot see is one nobody can approve — private memory was
    write-only until this was fixed, and silently so, because an empty queue
    and an unreachable one look identical.
    """
    payload = memory_request("GET", "/v1/proposals?limit=200",
                             service_env_fn=service_env_fn, report_fn=report_fn)
    if payload is None:
        return 1
    proposals = payload.get("proposals", [])
    if identity:
        proposals = [p for p in proposals
                     if p.get("scope", "household") == identity]
    if not proposals:
        print(f"no pending proposals{f' for {identity}' if identity else ''}")
        return 0
    for item in proposals:
        scope = item.get("scope", "household")
        print(f"{item['id']}  [{scope}]  {item.get('statement', '')}")
    print(f"\n{len(proposals)} pending — approve with: "
          f"cli/agentbox memory approve <id>")
    if not payload.get("as_operator"):
        _rep(report_fn, WARN, "listed without the operator review token, so private "
                              "proposals are hidden — check MEMORY_REVIEW_TOKEN")
    return 0


def memory_add(statement: str, scope: str = "", memory_type: str = "",
               supersedes: str = "",
               service_env_fn: Callable[[str, tuple[str, ...]], dict[str, str]] | None = None,
               report_fn: Callable[[str, str], None] | None = None) -> int:
    """Record something the operator states directly, with no review step.

    Reviewing your own statement is theatre: the review gate exists so the
    assistant cannot write its own durable memory, and a person typing a fact
    at a terminal is the evidence that gate is protecting. The bridge has had
    an operator-only write path since it was built and nothing ever called it
    — so the only way a fact could enter memory was for the assistant to guess
    it first and a human to agree. "Bin day is Wednesday" had no route in at
    all unless the assistant happened to propose it, and when it did guess, it
    guessed Tuesday.

    Household by default, because a fact worth typing by hand is usually one
    the whole house shares. Pass --scope <name> to keep it to one person.
    """
    statement = statement.strip()
    if not statement:
        _rep(report_fn, FAIL, "a statement is required")
        return 1
    payload: dict = {"statement": statement, "source": "stated by the operator"}
    if supersedes:
        payload["supersedes"] = supersedes
    if scope:
        payload["scope"] = scope
    if memory_type:
        payload["type"] = memory_type
    result = memory_request("POST", "/v1/memories", payload,
                            service_env_fn=service_env_fn, report_fn=report_fn)
    if result is None:
        return 1
    _rep(report_fn, OK, f"remembered [{result.get('scope', 'household')}]: "
                        f"{result.get('statement', statement)}")
    if result.get("replaced"):
        _rep(report_fn, OK, f"superseded: {result['replaced'].get('statement','')}")
    for candidate in result.get("possibly_supersedes", []):
        # Offered, never applied: an automatic supersession that is wrong
        # hides a true memory behind a false one and says nothing.
        _rep(report_fn, WARN, f"this may replace an existing memory — if so: "
                              f"cli/agentbox memory forget {candidate['id']}  "
                              f"(or re-add with --supersedes {candidate['id']})\n"
                              f"         existing: {candidate['statement'][:70]}")
    # The heuristic runs on the way in. If it disagrees, say so once rather
    # than overruling — the operator asked for a memory and gets one, but a
    # behaviour complaint stored as a fact is the thing worth a second look.
    if result.get("kind") == "feedback":
        _rep(report_fn, WARN, "that reads as feedback about behaviour rather than a "
                              "fact. It is stored as a memory because you asked for "
                              "one; `agentbox feedback` is the other list.")
    return 0


def memory_show(limit: int = 50,
                service_env_fn: Callable[[str, tuple[str, ...]], dict[str, str]] | None = None,
                report_fn: Callable[[str, str], None] | None = None) -> int:
    """What is actually stored, as opposed to what is waiting for review."""
    payload = memory_request("GET", f"/v1/memories?limit={int(limit)}",
                             service_env_fn=service_env_fn, report_fn=report_fn)
    if payload is None:
        return 1
    items = payload.get("memories", [])
    if not items:
        print("nothing remembered yet")
        return 0
    for item in items:
        print(f"{item['id']}  [{item.get('scope','household')}]  "
              f"{item.get('statement','')}")
        if item.get("edited_by_reviewer"):
            print(f"{'':38}(edited; originally: "
                  f"{item.get('original_statement','')[:60]})")
    print(f"\n{len(items)} remembered — remove one with: "
          f"cli/agentbox memory forget <id>")
    return 0


def memory_history(memory_id: str,
                   service_env_fn: Callable[[str, tuple[str, ...]], dict[str, str]] | None = None,
                   report_fn: Callable[[str, str], None] | None = None) -> int:
    """How a fact changed over time, oldest first.

    Reachable from any link in the chain, because the id somebody has is
    usually the one they saw in an old answer rather than the current one.
    """
    payload = memory_request(
        "GET", f"/v1/memories/{urllib.parse.quote(memory_id)}/history",
        service_env_fn=service_env_fn, report_fn=report_fn)
    if payload is None:
        return 1
    for item in payload.get("history", []):
        marker = {"approved": "now  ", "superseded": "was  ",
                  "forgotten": "wrong"}.get(item.get("status"), "?    ")
        print(f"{marker} {item.get('created_at','')[:10]}  "
              f"{item.get('statement','')}")
    print(f"\n{payload.get('length', 0)} version(s)")
    return 0


def memory_forget(memory_id: str, reason: str = "",
                  service_env_fn: Callable[[str, tuple[str, ...]], dict[str, str]] | None = None,
                  record_decision_fn: Callable[[str, str, str, str], None] | None = None,
                  report_fn: Callable[[str, str], None] | None = None) -> int:
    """Remove something already durable.

    Memory was append-only until this existed: a wrong fact stayed wrong
    forever, and these statements are read back as true, so a stale one does
    not sit inertly — it misinforms every answer that touches it.
    """
    payload = memory_request(
        "POST", f"/v1/memories/{urllib.parse.quote(memory_id)}/forget",
        {"reason": reason}, service_env_fn=service_env_fn, report_fn=report_fn)
    if payload is None:
        return 1
    if record_decision_fn:
        record_decision_fn("operator", "forget", "memory", reason)
    _rep(report_fn, OK, f"forgotten: {payload.get('statement', memory_id)}")
    return 0


def feedback_list(status: str = "open",
                  service_env_fn: Callable[[str, tuple[str, ...]], dict[str, str]] | None = None,
                  report_fn: Callable[[str, str], None] | None = None) -> int:
    """The improvement backlog: things to fix properly rather than remember.

    Deliberately an operator surface only. If the assistant could read this it
    would start explaining the behaviour instead of the behaviour changing,
    which is exactly the patch-around this split exists to prevent.
    """
    payload = memory_request("GET", f"/v1/feedback?status={urllib.parse.quote(status)}",
                             service_env_fn=service_env_fn, report_fn=report_fn)
    if payload is None:
        return 1
    items = payload.get("feedback", [])
    if not items:
        print(f"no {status} feedback")
        return 0
    for item in items:
        print(f"{item['id']}  [{item.get('status', 'open')}]  "
              f"{item.get('statement', '')}")
        if item.get("resolution"):
            print(f"            fixed by: {item['resolution']}")
    print(f"\n{len(items)} item(s). When you have actually changed a skill, "
          f"prompt or tool description:\n"
          f"  cli/agentbox feedback fold <id> --note 'what you changed'")
    return 0


def feedback_decide(action: str, feedback_id: str, note: str = "",
                    service_env_fn: Callable[[str, tuple[str, ...]], dict[str, str]] | None = None,
                    record_decision_fn: Callable[[str, str, str, str], None] | None = None,
                    report_fn: Callable[[str, str], None] | None = None) -> int:
    payload = memory_request(
        "POST", f"/v1/feedback/{urllib.parse.quote(feedback_id)}/{action}",
        {"note": note}, service_env_fn=service_env_fn, report_fn=report_fn)
    if payload is None:
        return 1
    # The highest-value record in the journal: a human said the behaviour was
    # wrong, and then said what they changed about it.
    if record_decision_fn:
        record_decision_fn("operator", f"feedback_{action}", "assistant_behaviour", note)
    _rep(report_fn, OK, f"{payload.get('status', action)}: "
                        f"{payload.get('statement', feedback_id)}")
    return 0


def memory_decide(action: str, proposal_id: str, reason: str = "",
                  service_env_fn: Callable[[str, tuple[str, ...]], dict[str, str]] | None = None,
                  record_decision_fn: Callable[[str, str, str, str], None] | None = None,
                  report_fn: Callable[[str, str], None] | None = None) -> int:
    body = {"reason": reason} if action == "reject" else None
    payload = memory_request("POST", f"/v1/proposals/{urllib.parse.quote(proposal_id)}/{action}", body,
                             service_env_fn=service_env_fn, report_fn=report_fn)
    if payload is None:
        return 1
    # A rejected memory proposal is a correction — the clearest "no, not that"
    # the system ever gets, and worth strictly more than an approval.
    if record_decision_fn:
        record_decision_fn("operator", action, "memory_proposal", reason)
    _rep(report_fn, OK, f"{payload.get('status', action)}: {payload.get('statement', proposal_id)}")
    return 0
