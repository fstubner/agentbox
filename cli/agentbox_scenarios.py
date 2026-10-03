"""Household scenarios: does the assistant actually help, and how much of it works?

`smoke` answers "is the plumbing sound" — every seam, every refusal, every
guardrail. It passes on a box that helps nobody, because a house with no
devices and an unconsented Drive still has intact seams. This answers the
different question: **if a person asked the ten things they would actually
ask, how many could the assistant answer today?**

The distinction matters because the two failures look identical from inside
`smoke` and opposite from the kitchen. "controllable is empty" and "Drive
403s" are not bugs — the seams are fine — they are the difference between
installed and useful, and nothing measured that difference until a week of
the journal showed `whoami` outnumbering every real tool.

Each scenario is a sentence a household member might say, the tool that would
answer it, and a verdict function that reads the live result. A scenario is:

- **ready** — it answered, with something real behind it (a calendar with
  events, a house with controllable devices, a mailbox that searched);
- **empty** — it worked but there is nothing there yet (no events today, no
  controllable devices). Honest, not a failure, but not usefulness either;
- **blocked** — it could not answer, with the reason a person can act on
  (needs Drive re-consent, needs a device onboarded, needs SMTP).

The score is `ready / total`. It is meant to start low and climb as the
config gaps close — a target, not a grade. Run it weekly beside the journal
read below.

This is deliberately data, not cleverness: adding a scenario is appending one
entry, so the set grows with what the household actually asks for.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

READY, EMPTY, BLOCKED = "ready", "empty", "blocked"


class Scenario:
    """One thing a person would ask, and how to tell whether it was answered.

    `verdict(ok, payload)` returns (state, detail). It never raises — a
    scenario that throws would read as a suite bug rather than a house that
    cannot yet answer, which is the exact confusion this file exists to end.
    """

    def __init__(self, asked: str, tool: str, args: dict,
                 verdict: Callable[[bool, Any], tuple[str, str]],
                 needs: str = "", prepare: Callable | None = None) -> None:
        self.asked = asked
        self.tool = tool
        self.args = args
        self.verdict = verdict
        # What closes the gap when this is blocked or empty. Printed as the
        # next action, so the report doubles as a to-do list.
        self.needs = needs
        # Optional: prepare(tool_call, token) -> args or None. For scenarios
        # that need a real object to act on — triage needs an actual message
        # id, or it tests Gmail's error path instead of the dispatch path.
        # Returning None means there is nothing to act on: verdict on
        # (True, None) should read that as empty.
        self.prepare = prepare


def _has_items(label: str, key: str | None = None, needs: str = ""):
    """Verdict: ready if the payload has rows, empty if it worked but is bare.

    A dict payload requires `key`. The first live run scored "ready: 4
    lights" against a house with zero lights, because the bridge returns
    {"entities": [], "total": 0, ...} and len() of that dict counted its
    keys — the scenario runner confidently reporting readiness that did not
    exist, in the tool built to end exactly that. A dict with no key is now
    an unexpected-shape verdict, never a count.
    """
    def check(ok: bool, payload: Any) -> tuple[str, str]:
        if not ok:
            return BLOCKED, str(payload)[:120]
        if isinstance(payload, dict):
            if not key:
                return BLOCKED, f"unexpected shape: dict with no '{label}' key declared"
            rows = payload.get(key)
            if not isinstance(rows, list):
                return BLOCKED, f"unexpected shape: no '{key}' list in reply"
        elif isinstance(payload, list):
            rows = payload
        else:
            return BLOCKED, f"unexpected shape: {type(payload).__name__}"
        if rows:
            return READY, f"{len(rows)} {label}"
        return EMPTY, f"no {label} yet" + (f" — {needs}" if needs else "")
    return check


def _recent_message_id(tool_call, token):
    """Find a real message with readable text to summarise.

    Without this, the triage scenario sent a fake id and Gmail's 400 arrived
    before the dispatch ever ran — the one scenario meant to exercise the
    model router was testing Gmail's error path instead, while reporting the
    router as the thing blocked.
    """
    ok, payload = tool_call("agentbox-mcp", "search_gmail",
                            {"query": "in:anywhere", "max_results": 5}, token)
    if not ok:
        return f"Gmail search failed: {str(payload)[:90]}"
    messages = payload.get("messages") if isinstance(payload, dict) else payload
    if not isinstance(messages, list) or not messages:
        return None
    # Screen candidates through clean_gmail — bridge calls, no model
    # inference — because the newest message anywhere is often a calendar
    # invite or image-only mail with no extractable text, and "that one
    # message was unreadable" must not score the whole summary path blocked.
    for message in messages:
        message_id = message.get("id") or message.get("message_id")
        if not message_id:
            continue
        ok, cleaned = tool_call("agentbox-mcp", "clean_gmail",
                                {"message_id": message_id}, token)
        if ok and isinstance(cleaned, dict) and str(
                cleaned.get("clean_text") or cleaned.get("untrusted_text")
                or cleaned.get("text") or "").strip():
            return {"message_id": message_id}
    return f"searched {len(messages)} recent messages, none had readable text"


# The set. Framed in the household's words, not the tool's. Ordered roughly by
# how often a person would reach for each.
SCENARIOS = [
    Scenario(
        "What's on my calendar today?",
        "list_calendar_events", {"view": "lean"},
        # Google's shape: the events ride under "items". The first run of
        # this suite reported "3 events" that were three dict keys.
        _has_items("events", key="items",
                   needs="a busier day, or check the calendar is shared"),
        needs="Google connected (it is) — this one is live"),

    Scenario(
        "Am I free this afternoon?",
        "calendar_freebusy",
        {"timeMin": "2000-01-01T12:00:00Z", "timeMax": "2000-01-01T18:00:00Z",
         "items": ["primary"]},
        lambda ok, p: (READY, "free/busy answered") if ok
        else (BLOCKED, str(p)[:120]),
        needs="Google connected"),

    Scenario(
        "Did anything important land in my inbox?",
        "search_gmail", {"query": "is:unread newer_than:2d", "max_results": 5},
        # Gmail's shape: hits ride under "messages". Both earlier runs of
        # this suite read the raw dict, never matched, and reported an empty
        # inbox they had not actually looked inside.
        _has_items("recent unread", key="messages",
                   needs="quiet inbox — that is the good case"),
        needs="Google connected"),

    Scenario(
        "Summarise that email without me reading it.",
        "clean_gmail", {},
        # The cleaned body of a real message found by prepare, which the main
        # model then summarises. This used to go through triage_email and a
        # small worker, which kept the body out of the conversation. That route
        # is retired with the router (see router/README.md). The body now
        # enters the conversation, but the question still has an answer, and
        # this checks that it does.
        lambda ok, p: (READY, f"{len(p['clean_text'])} characters ready to summarise")
        if ok and isinstance(p, dict) and p.get("clean_text")
        else (BLOCKED, str(p)[:110]),
        needs="Gmail connected",
        prepare=_recent_message_id),

    Scenario(
        "What can you turn on or off in the house?",
        "list_home_entities", {"view": "lean"},
        lambda ok, p: (BLOCKED, str(p)[:120]) if not ok
        else (READY, f"{len(p.get('controllable', []))} controllable")
        if isinstance(p, dict) and p.get("controllable")
        else (EMPTY, f"{p.get('total', 0)} entities, none controllable"),
        needs="mark devices controllable in Home Assistant, or onboard a plug/light"),

    Scenario(
        "Turn the landing light off.",
        "list_home_entities", {"domain": "light", "view": "lean"},
        _has_items("lights", key="entities"),
        needs="a light integration (Hue: press the bridge button) — none present yet"),

    Scenario(
        "Read the shopping list back to me.",
        "list_speakers", {},
        lambda ok, p: (BLOCKED, str(p)[:120]) if not ok
        else (READY, f"{len(p.get('speakers', []))} speakers"
              + (" (quiet hours)" if p.get("quiet_hours") else ""))
        if isinstance(p, dict) and p.get("speakers")
        else (EMPTY, "no speakers paired"),
        needs="a paired speaker (both Echoes are paired) — live"),

    Scenario(
        "What do you remember about me?",
        "search_memories", {"limit": 10},
        _has_items("memories", key="memories"),
        needs="approve some memories in the portal as they are proposed"),

    Scenario(
        "What's on the to-do list?",
        "list_tasks", {"view": "lean"},
        _has_items("tasks", needs="add tasks — the backend is up"),
        needs="Vikunja (up) — live"),

    Scenario(
        "Find that lease PDF in my Drive.",
        "search_drive", {"query": "lease", "max_results": 5},
        # "no match" was a lie. With the drive.file scope the assistant can
        # only see files it created itself, so a person with a full Drive gets
        # zero results and a report that reads like an empty Drive. The
        # scenario has to name the scope or it teaches the wrong lesson.
        _has_items("files", key="files",
                   needs="nothing is visible under the drive.file scope — see "
                         "below"),
        needs="the granted scope is drive.file, which sees ONLY files Agentbox "
              "created — not your own documents. Granting drive.readonly at "
              "consent time would let it search your real Drive; that is a "
              "deliberate privacy decision, not a bug to fix"),
]


def _run_one(scenario, tool_call, token: str) -> tuple[str, str]:
    """One scenario to a (state, detail) verdict. Never raises.

    prepare's three-way contract: a dict is the arguments to use; None means
    the house has nothing to act on (empty, honestly); a string is why
    preparation itself failed (blocked, with the reason). The distinction
    matters because "no mail to summarise" and "Gmail is down" demand
    different actions from different people.
    """
    args = scenario.args
    if scenario.prepare is not None:
        try:
            prepared = scenario.prepare(tool_call, token)
        except Exception as exc:  # noqa: BLE001 — a prepare bug is not a house verdict
            return BLOCKED, f"scenario prepare errored: {type(exc).__name__}"
        if prepared is None:
            return EMPTY, "nothing to act on yet"
        if isinstance(prepared, str):
            return BLOCKED, prepared[:120]
        args = prepared
    ok, payload = tool_call("agentbox-mcp", scenario.tool, args, token)
    try:
        return scenario.verdict(ok, payload)
    except Exception as exc:  # noqa: BLE001 — a verdict bug is not a house verdict
        return BLOCKED, f"scenario check errored: {type(exc).__name__}"


def run(tool_call, token: str) -> dict:
    """Run every scenario against the live gateway. Pure data back, no I/O —
    the caller prints, so this stays testable with a fake tool_call.
    """
    results = []
    for s in SCENARIOS:
        state, detail = _run_one(s, tool_call, token)
        results.append({"asked": s.asked, "tool": s.tool, "state": state,
                        "detail": detail, "needs": s.needs})
    ready = sum(1 for r in results if r["state"] == READY)
    return {"results": results, "ready": ready, "total": len(results),
            "empty": sum(1 for r in results if r["state"] == EMPTY),
            "blocked": sum(1 for r in results if r["state"] == BLOCKED)}


# --- the journal side: what actually got used --------------------------------

# Tools that answer a household question, as opposed to the assistant managing
# itself. The split is the whole point of the usage read: 400 calls that are
# all whoami and propose_change is a system talking to itself, not a household
# being helped. Kept here beside the scenarios so the two notions of "useful"
# cannot drift.
HOUSEHOLD_TOOLS = frozenset({
    "search_gmail", "read_gmail", "clean_gmail",
    "list_calendar_events", "calendar_freebusy", "create_gmail_draft",
    "search_drive", "read_drive_file", "list_recent_drive_files",
    "set_home_light", "activate_home_scene", "set_home_climate",
    "list_home_entities", "get_home_entity", "speak_aloud",
    "list_tasks", "find_or_create_task", "create_task", "complete_task",
    "search_memories",
})


def usage_summary(records: list[dict]) -> dict:
    """Fold the outcome journal into household vs self-management vs failures.

    `records` is the parsed journal — passed in rather than read here so a
    test can hand it a fixture and the caller owns the file path.
    """
    household = failures = self_mgmt = 0
    by_tool: dict[str, int] = {}
    identities: dict[str, int] = {}
    for r in records:
        tool = str(r.get("tool", ""))
        if not tool:
            continue  # operator-decision records have no tool
        outcome = r.get("outcome", "ok")
        if outcome in ("error", "denied", "invalid"):
            failures += 1
        if tool in HOUSEHOLD_TOOLS and outcome == "ok":
            household += 1
            by_tool[tool] = by_tool.get(tool, 0) + 1
            if r.get("identity"):
                identities[r["identity"]] = identities.get(r["identity"], 0) + 1
        elif outcome == "ok":
            self_mgmt += 1
    return {"household_ok": household, "self_management_ok": self_mgmt,
            "failures": failures,
            "top_household_tools": sorted(
                by_tool.items(), key=lambda kv: -kv[1])[:6],
            "by_identity": identities}
