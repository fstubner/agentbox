"""`agentbox smoke` and `agentbox scenarios`: real workflows against the running stack."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import agentbox_memory
from agentbox_common import FAIL, OK, WARN, policy_state_dir, report, service_env_values
from agentbox_policy import load_tool_map

# --- smoke: end-to-end workflows against the running system -----------------
#
# `doctor` checks that services are up. This checks that workflows succeed.
# The tests in tests/ run against fixtures, so this is what covers the real path
# from agentbox-mcp to the bridges and upstream. A service can pass every
# readiness probe and still refuse every call.
#
# It runs against live accounts, so it follows these rules.
#   - Vikunja holds test data, so task workflows create and then clean up.
#   - Google holds real data, so only reads run. Nothing drafts, labels or
#     archives.
#   - Memory proposals do nothing until approved, so proposing is safe. The
#     proposal is rejected through the operator's review path.

# Port and the environment variable holding each server's token.
MCP_PORTS = {"agentbox-mcp": 3465}
MCP_TOKEN_VARS = {"agentbox-mcp": "AGENTBOX_MCP_SHARED_TOKEN"}


def smoke_token(service: str) -> str:
    """The gateway may be identity-aware, in which case the shared token is
    refused and smoke must present a real identity's token instead."""
    values = service_env_values(service, ("AGENTBOX_MCP_SHARED_TOKEN",
                                          "AGENTBOX_IDENTITIES"))
    identities = values.get("AGENTBOX_IDENTITIES", "").strip()
    if identities:
        first = identities.split(",")[0]
        if ":" in first:
            return first.split(":", 1)[1].strip()
    return values.get("AGENTBOX_MCP_SHARED_TOKEN", "")


def mcp_token(service: str) -> str:
    var = MCP_TOKEN_VARS[service]
    return service_env_values(service, (var,)).get(var, "")


def mcp_rpc(service: str, method: str, params: dict | None = None,
            token: str | None = None, timeout: float = 30) -> dict:
    """One JSON-RPC call to a live MCP. Returns the parsed envelope."""
    port = MCP_PORTS[service]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params or {}}).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/mcp", data=body,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read())
        except Exception:
            return {"error": {"code": exc.code, "message": exc.reason}}
    except urllib.error.URLError as exc:
        # Nothing listening. Optional services such as Home Assistant are not
        # deployed everywhere, so report that rather than crash.
        return {"error": {"code": -1, "message": f"not reachable: {exc.reason}",
                          "unreachable": True}}


def tool_call(service: str, name: str, arguments: dict, token: str) -> tuple[bool, Any]:
    """Call a tool. Returns (ok, payload) where payload is the decoded result."""
    envelope = mcp_rpc(service, "tools/call",
                       {"name": name, "arguments": arguments}, token)
    if "error" in envelope:
        return False, envelope["error"].get("message", envelope["error"])
    result = envelope.get("result", {})
    if result.get("isError"):
        content = result.get("content") or [{}]
        return False, content[0].get("text", "tool reported an error")
    if "structuredContent" in result:
        return True, result["structuredContent"]
    content = result.get("content") or [{}]
    text = content[0].get("text", "")
    try:
        return True, json.loads(text)
    except Exception:
        return True, text


def smoke() -> int:
    """Drive the assistant's real workflows and report what works."""
    failures = 0
    tokens = {service: smoke_token(service) for service in MCP_PORTS}

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        if ok:
            report(OK, f"{name}{(': ' + detail) if detail else ''}")
        else:
            failures += 1
            report(FAIL, f"{name}{(': ' + detail) if detail else ''}")

    print("\n-- protocol --")
    live = []
    for service in MCP_PORTS:
        envelope = mcp_rpc(service, "server/discover", token=tokens[service])
        error = envelope.get("error") or {}
        if isinstance(error, dict) and error.get("unreachable"):
            report(WARN, f"{service} not deployed; skipping its checks")
            continue
        live.append(service)
        versions = (envelope.get("result") or {}).get("supportedVersions") or []
        check(f"{service} discovery", "2026-07-28" in versions,
              ", ".join(versions) if versions else str(envelope.get("error", "no response")))

    # With no token configured the server must refuse rather than skip the
    # check. It holds bridge tokens, so failing open would hand them to any
    # local process.
    for service in live:
        envelope = mcp_rpc(service, "tools/list", token=None)
        check(f"{service} refuses unauthenticated calls", "error" in envelope,
              "ANSWERED WITHOUT A TOKEN" if "error" not in envelope else "")

    print("\n-- tools --")
    tool_map = load_tool_map()
    for service in live:
        envelope = mcp_rpc(service, "tools/list", token=tokens[service])
        tools = (envelope.get("result") or {}).get("tools") or []
        names = [t.get("name") for t in tools]
        check(f"{service} lists tools", bool(names), f"{len(names)} tools")
        unmapped = [n for n in names if n not in tool_map]
        # `not unmapped` is true for an empty list, so an empty tool list must
        # not pass as fully mapped.
        check(f"{service} tools all tiered", bool(names) and not unmapped,
              f"unmapped: {', '.join(unmapped)}" if unmapped else
              ("" if names else "no tools to check"))

    print("\n-- task workflow (test data, cleans up) --")
    ok, projects = tool_call("agentbox-mcp", "list_projects", {}, tokens["agentbox-mcp"])
    check("list projects", ok, "" if ok else str(projects))
    project_id = None
    if ok and isinstance(projects, list) and projects:
        project_id = projects[0].get("id")

    # A tool that declares an argument required must say so when it is absent,
    # rather than failing somewhere inside the handler.
    ok, message = tool_call("agentbox-mcp", "find_or_create_task",
                            {"title": "missing project_id"}, tokens["agentbox-mcp"])
    check("missing required argument is named", not ok and "project_id" in str(message),
          str(message)[:80])

    stamp = time.strftime("%H%M%S")
    title = f"agentbox smoke {stamp}"
    created = None
    if project_id:
        ok, created = tool_call("agentbox-mcp", "find_or_create_task",
                                {"project_id": project_id, "title": title},
                                tokens["agentbox-mcp"])
        check("create task", ok, "" if ok else str(created))
    else:
        check("create task", False, "no project available to create in")
    task_id = (created or {}).get("id") if isinstance(created, dict) else None

    if task_id:
        ok, found = tool_call("agentbox-mcp", "list_tasks", {"view": "lean"},
                              tokens["agentbox-mcp"])
        titles = [t.get("title") for t in (found or [])] if isinstance(found, list) else []
        check("created task is listed", title in titles,
              "" if title in titles else "not found in list_tasks")

        ok, done = tool_call("agentbox-mcp", "complete_task", {"task_id": task_id},
                             tokens["agentbox-mcp"])
        check("complete task (cleanup)", ok, "" if ok else str(done))

    print("\n-- projection --")
    ok, lean = tool_call("agentbox-mcp", "list_tasks", {"view": "lean"}, tokens["agentbox-mcp"])
    ok2, full = tool_call("agentbox-mcp", "list_tasks", {"view": "full"}, tokens["agentbox-mcp"])
    if ok and ok2:
        lean_bytes, full_bytes = len(json.dumps(lean)), len(json.dumps(full))
        check("lean is smaller than full", lean_bytes < full_bytes,
              f"{full_bytes} B -> {lean_bytes} B")
    else:
        check("lean is smaller than full", False, "could not fetch both views")

    print("\n-- policy gate --")
    # Gated by capability home_control_climate. The call must be refused. This
    # is the one check where a successful call is a failure.
    ok, message = tool_call("agentbox-mcp", "set_home_climate",
                            {"entity_id": "climate.smoke", "temperature": 20},
                            tokens["agentbox-mcp"])
    check("gated tool is refused without a grant", not ok,
          "MODIFIED WITHOUT APPROVAL" if ok else "recorded for approval")
    pending = Path(policy_state_dir()) / "pending" / "set_home_climate.json"
    check("refusal is recorded for the operator", pending.exists())
    if pending.exists():
        try:
            pending.unlink()
        except OSError as exc:
            report(WARN, f"could not clear smoke approval request: {exc}")

    print("\n-- memory (proposals stay inert) --")
    ok, created_proposal = tool_call("agentbox-mcp", "propose_memory",
                                     {"statement": f"agentbox smoke test {stamp}"},
                                     tokens["agentbox-mcp"])
    check("propose memory", ok, "" if ok else str(created_proposal))
    ok, proposals = tool_call("agentbox-mcp", "list_memory_proposals", {},
                              tokens["agentbox-mcp"])
    check("proposal is queued for review", ok and bool(proposals),
          "" if ok else str(proposals))

    # Reject it again as the operator. This cleans up and also tests the part
    # of the review gate the assistant cannot reach. Rejection needs a token
    # that lives here and not in any container.
    proposal_id = (created_proposal or {}).get("id") if isinstance(created_proposal, dict) else None
    if proposal_id:
        rejected = agentbox_memory.memory_decide("reject", proposal_id, reason="agentbox smoke") == 0
        check("operator can reject the proposal", rejected)
    else:
        report(WARN, "no proposal id returned; clean up with `agentbox memory list`")

    print("\n-- builder (proposes code; must never ship it) --")
    ok, listing = tool_call("agentbox-mcp", "list_repo_files",
                            {"prefix": "docs/", "limit": 5}, tokens["agentbox-mcp"])
    check("assistant can read the repo", ok,
          f"{(listing or {}).get('total', 0)} files under docs/" if ok else str(listing)[:80])

    # Test the containment against the live system. A pass here means the
    # assistant cannot propose a change to the policy that governs it.
    for guarded in ("policies/approval-policy.yaml",
                    "services/templates/mcp/policy_gate.py",
                    "cli/agentbox"):
        ok, message = tool_call("agentbox-mcp", "propose_change", {
            "branch": "smoke-must-be-refused", "message": "smoke test",
            "files": [{"path": guarded, "content": "smoke\n"}]},
            tokens["agentbox-mcp"])
        check(f"refuses to edit {guarded}", not ok,
              "PROPOSED A CHANGE TO ITS OWN GUARDRAILS" if ok else "refused")

    ok, message = tool_call("agentbox-mcp", "propose_change", {
        "branch": "smoke-escape", "message": "smoke test",
        "files": [{"path": "../../etc/passwd", "content": "smoke\n"}]},
        tokens["agentbox-mcp"])
    check("refuses paths outside the repo", not ok,
          "WROTE OUTSIDE THE REPO" if ok else "refused")

    print("\n-- self-reflection --")
    ok, window = tool_call("agentbox-mcp", "review_own_activity", {"days": 7},
                           tokens["agentbox-mcp"])
    check("assistant can read its own activity", ok, "" if ok else str(window))
    if ok and isinstance(window, dict):
        tools_seen = window.get("tools") or {}
        # This run made the calls above, so an empty window means the journal
        # is not being written and reflection has nothing to read.
        check("journal has this run's calls in it", bool(tools_seen),
              f"{window.get('total_calls', 0)} calls in {window.get('window_days')}d")
        # The privacy property, checked against live data rather than a fixture.
        serialised = json.dumps(window)
        leaked = [word for word in ("statement", "@", "subject:") if word in serialised]
        check("no call content in the summary", not leaked,
              f"found: {leaked}" if leaked else "counts only")

    print("\n-- google (read-only) --")
    ok, labels = tool_call("agentbox-mcp", "list_gmail_labels", {},
                           tokens["agentbox-mcp"])
    check("list gmail labels", ok, f"{len(labels)} labels" if ok and isinstance(labels, list) else str(labels)[:80])
    ok, events = tool_call("agentbox-mcp", "list_calendar_events",
                           {"view": "lean"}, tokens["agentbox-mcp"])
    check("list calendar events", ok,
          f"{len(events)} events" if ok and isinstance(events, list) else str(events)[:80])

    print()
    if failures:
        report(FAIL, f"smoke: {failures} workflow(s) failed")
        return 1
    report(OK, "smoke: all workflows passed")
    return 0


def scenarios() -> int:
    """Of the things a person would ask, how many can the assistant answer
    today, and what would close each remaining gap?

    `smoke` checks the seams hold. This checks usefulness. Run it weekly, and
    the score should rise as gaps close.
    """
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "agentbox_scenarios", Path(__file__).resolve().parent / "agentbox_scenarios.py")
    lib = _ilu.module_from_spec(spec)
    # See the note in agentbox-portal's _sibling: a module loaded by path must
    # be in sys.modules before it executes, or any dataclass inside it fails.
    sys.modules["agentbox_scenarios"] = lib
    spec.loader.exec_module(lib)

    token = smoke_token("agentbox-mcp")
    if not token:
        report(FAIL, "no gateway token found; is agentbox-mcp configured?")
        return 1
    print("-- can the assistant answer what a person would ask? --\n")
    outcome = lib.run(tool_call, token)
    icon = {lib.READY: "[ok]  ", lib.EMPTY: "[  ..]", lib.BLOCKED: "[FAIL]"}
    for r in outcome["results"]:
        print(f"{icon[r['state']]} {r['asked']}")
        print(f"        {r['state']}: {r['detail']}")
        if r["state"] != lib.READY and r["needs"]:
            print(f"        next: {r['needs']}")
    print(f"\nscore: {outcome['ready']}/{outcome['total']} ready, "
          f"{outcome['empty']} empty, {outcome['blocked']} blocked")

    journal = Path(os.environ.get(
        "AGENTBOX_LOG_DIR",
        os.path.expanduser("~/.local/state/agentbox/logs"))) / "agentbox-mcp-outcomes.jsonl"
    if journal.exists():
        records = []
        for line in journal.read_text(encoding="utf-8").splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
        summary = lib.usage_summary(records)
        print("\n-- and what actually got used (whole journal) --")
        print(f"household calls: {summary['household_ok']}   "
              f"self-management: {summary['self_management_ok']}   "
              f"failures: {summary['failures']}")
        if summary["top_household_tools"]:
            print("top household tools: " + ", ".join(
                f"{t} x{n}" for t, n in summary["top_household_tools"]))
        if summary["by_identity"]:
            print("by person: " + ", ".join(
                f"{k}: {v}" for k, v in summary["by_identity"].items()))
        if summary["household_ok"] < summary["self_management_ok"]:
            report(WARN, "the assistant mostly talks to itself, with more "
                         "self-management than household calls. The score "
                         "above says what to unblock.")
    return 0 if outcome["ready"] > 0 else 1
