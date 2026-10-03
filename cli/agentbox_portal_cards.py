"""Cards on the portal's home and Operations pages."""
from __future__ import annotations

import html
from typing import Any

_portal: Any = None


def bind(portal_mod: Any) -> None:
    global _portal
    _portal = portal_mod


def render_status_card() -> str:
    """What is actually running, rather than a paragraph about why it matters."""
    snap = _portal.agentbox_status.cached()
    age = _portal.time_ago(max(0, _portal.now() - snap.taken_at))

    problems = sorted(snap.problems,
                      key=lambda c: 0 if c.severity == _portal.agentbox_status.FAIL else 1)
    if problems:
        worst = "fail" if snap.failing else "warn"
        headline = (f"{len(problems)} thing{'s' if len(problems) != 1 else ''} "
                    f"need{'' if len(problems) != 1 else 's'} attention")
        note = ("<p class=sub style='margin:.5rem 0 0'>An evaluation is "
                "running. Benchmarks that need the whole memory budget stop "
                "the assistant on purpose and start it again afterwards, so "
                "some of this may be expected, but it is listed rather than "
                "hidden, because a page that goes quiet during evaluations "
                "cannot be trusted during one.</p>") if snap.evaluating else ""
    else:
        worst, headline = "ok", "Everything is running"
        note = ("<p class=sub style='margin:.5rem 0 0'>An evaluation is "
                "running, and nothing is down.</p>") if snap.evaluating else ""

    rows = [f"<div class=row><span class='dot {worst}'></span>"
            f"<span class=name><b>{html.escape(headline)}</b></span>"
            f"<span class=when>checked {html.escape(age)}</span></div>"]

    for check in problems:
        rows.append(
            f"<div class=row><span class='dot {check.level}'></span>"
            f"<span class=name>{html.escape(check.name)}"
            f"<div class=sub style='margin:0'>{html.escape(check.detail)}</div>"
            f"</span></div>")

    body = "".join(rows) + note
    if not snap.services:
        body += ("<p class=sub style='margin:.6rem 0 0'>No containers are "
                 "visible from here, so this list is incomplete rather than "
                 "empty, because Docker did not answer.</p>")
    else:
        lines = []
        for service in snap.services:
            if not getattr(service, "deployed", True):
                level, note = "warn", service.status
            elif not service.running:
                level, note = "fail", "not running"
            elif service.stale:
                level, note = "warn", "running older code than is committed"
            else:
                level, note = "ok", service.status
            lines.append(
                f"<div class=row><span class='dot {level}'></span>"
                f"<span class=name>{html.escape(service.label)}</span>"
                f"<span class=when>{html.escape(note)}</span></div>")
        body += ("<div style='margin:.7rem 0 0;padding-top:.5rem;"
                 "border-top:1px solid var(--card-border)'>" + "".join(lines) + "</div>")

    return f"<div class=card><b>How things are</b><div style='margin:.5rem 0 0'>{body}</div></div>"


def render_onboarding_card() -> str:
    """Onboarding that has been filled in and is waiting on an admin."""
    waiting = _portal.submitted_invites()
    if not waiting:
        return ""
    rows = []
    for invite in waiting:
        who = invite["identity"] or invite["id"]
        called = invite["display_name"]
        wants = ", ".join(invite["connectors"]) or "nothing personal"
        if invite["requested"]:
            action = "<span class=sub>approved \u2014 setting up\u2026</span>"
            level = "ok"
        else:
            action = (
                f"<form method=post action=/admin/onboard style='margin:0'>"
                f"<input type=hidden name=token_id "
                f"value='{html.escape(invite['id'])}'>"
                f"<button type=submit>Finish setting them up</button></form>")
            level = "warn"
        note = ""
        if "google" in invite["connectors"] and not invite["google_ready"]:
            note = ("<div class=sub>They did not finish the Google consent "
                    "step, so they would share the household bridge rather "
                    "than get their own. They can connect Google themselves "
                    "afterwards, from their own portal.</div>")
            level = "warn"
        says = ""
        if called:
            says = (f"<div class=sub>calls themselves "
                    f"&ldquo;{html.escape(called)}&rdquo;</div>")
        rows.append(
            f"<div class=row><span class='dot {level}'></span>"
            f"<span class=name>{html.escape(who)}{says}"
            f"<div class=sub>asked for: {html.escape(wants)}</div>{note}</span>"
            f"<span class=when>{action}</span></div>")
    return (f"<div class=card><b>Waiting to be set up</b>"
            f"<div class=sub style='margin:.4rem 0 .6rem'>They filled in the "
            f"invite form. Finishing creates their accounts and their own "
            f"bridge, so the assistant reaches their mail with their "
            f"credential and nobody else's.</div>{''.join(rows)}</div>")


def render_proposals_card() -> str:
    """Invitations the assistant drafted, waiting on a person."""
    waiting = _portal.agentbox_onboarding.proposals()
    if not waiting:
        return ""
    taken = _portal.known_identities()
    rows = []
    for draft in waiting:
        name = str(draft.get("identity", ""))
        display = str(draft.get("display_name", "")) or name
        ways = []
        if draft.get("address"):
            ways.append(html.escape(str(draft["address"])))
        if draft.get("discord_user_id"):
            ways.append("Discord")
        collides = name in taken
        note = ""
        if collides:
            note = ("<div class=sub>Somebody by that name already lives here. "
                    "Sending this would be inviting a stranger to become "
                    "them, so it is refused.</div>")
        elif not ways:
            note = ("<div class=sub>No address and no Discord id, so there is "
                    "nowhere to send it.</div>")
        buttons = (
            f"<form method=post action=/admin/proposal style='margin:0'>"
            f"<input type=hidden name=id value='{html.escape(str(draft['id']))}'>"
            "<button type=submit class=danger name=action value=discard "
            "onclick=\"return confirm('Discard this proposal branch?');\">Discard</button>"
            + ("" if (collides or not ways) else
               "<button type=submit name=action value=send>Send it</button>")
            + "</form>")
        rows.append(
            f"<div class=row><span class='dot {'warn' if (collides or not ways) else 'ok'}'></span>"
            f"<span class=name>{html.escape(display)} "
            f"<span class=sub>as {html.escape(name)}"
            f"{' · ' + ' · '.join(ways) if ways else ''}</span>"
            f"{note}</span><span class=when>{buttons}</span></div>")
    return (f"<div class=card><b>The assistant drafted an invitation</b>"
            f"<div class=sub style='margin:.4rem 0 .6rem'>Nothing has been "
            f"sent. It can write an invitation and pick how it travels; "
            f"deciding who lives here is yours.</div>{''.join(rows)}</div>")


def render_new_invite_card(error: str = "") -> str:
    """Start somebody's onboarding without opening a terminal."""
    note = ""
    if error:
        note = f"<div class=sub style='margin-top:.6rem'>{error}</div>"

    waiting = []
    for record in _portal.agentbox_onboarding.outstanding():
        url = _portal.agentbox_onboarding.invite_url(
            record, _portal.invite_public_host(), _portal.INVITE_PORT)
        hours = max(0, (int(record.get("expires_at", 0)) - _portal.now()) // 3600)
        waiting.append(
            f"<div class=row><span class='dot warn'></span>"
            f"<span class=name>{html.escape(str(record.get('identity', '')))}"
            f"<div class=sub>not opened yet \u00b7 expires in {hours}h</div>"
            f"<div class=sub style='word-break:break-all'>"
            f"{html.escape(url)}</div></span></div>")
    outstanding = ""
    if waiting:
        outstanding = (
            f"<div class=sub style='margin:.9rem 0 .3rem'><b>Waiting to be "
            f"opened</b> \u2014 each link works once, and only for the person "
            f"you send it to.</div>{''.join(waiting)}")

    return (
        f"<div class=card><b>Invite somebody new</b>"
        f"<div class=sub style='margin:.4rem 0 .7rem'>They get a link to a "
        f"short form. Nothing is created until you approve it here "
        f"afterwards.</div>"
        f"<form method=post action=/admin/invite-create>"
        f"<input name=account_name placeholder='name, e.g. sam' autocomplete=off "
        f"style='padding:.4rem .5rem;margin:0 .4rem .4rem 0'>"
        f"<input name=email type=email placeholder='email (optional)' "
        f"autocomplete=off style='padding:.4rem .5rem;margin:0 .4rem .4rem 0'>"
        f"<input name=discord_user_id placeholder='Discord id (optional)' "
        f"autocomplete=off style='padding:.4rem .5rem;margin:0 .4rem .4rem 0'>"
        f"<button class=yes>Send invitation</button></form>{note}"
        f"{outstanding}</div>")
