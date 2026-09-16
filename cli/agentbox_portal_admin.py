"""agentbox_portal_admin — operations and administration views.

Follows the established agentbox modular CLI pattern:
- Dedicated module with clean functional boundaries (< 300 LOC)
- Preserves exact settings rendering, household device controls, invite forms, and admin shell
"""
from __future__ import annotations

import html
import os
from typing import Any

_portal: Any = None


def bind(portal_mod: Any) -> None:
    global _portal
    _portal = portal_mod


def render_people_card(flash_error: str = "") -> str:
    """Who lives here, how each of them gets in, and a way to add somebody."""
    emails = _portal.email_map()
    by_identity = {name: address for address, name in emails.items()}
    reachable = _portal.discord_identities()
    assistant_side = {n.strip() for n in os.environ.get(
        "AGENTBOX_IDENTITY_NAMES", "").replace(" ", ",").split(",") if n.strip()}
    known = sorted(set(by_identity) | _portal.admin_names() | reachable | assistant_side)

    rows = []
    for identity in known:
        address = by_identity.get(identity, "")
        ways = []
        if address:
            ways.append(html.escape(address))
        if identity in reachable:
            ways.append("Discord")
        connected = any(c["connected"] for c in _portal.connector_status(identity))
        if ways:
            level = "ok"
            detail = " · ".join(ways)
        else:
            level = "warn"
            detail = "no way to receive a sign-in link"
        role = " (admin)" if identity in _portal.admin_names() else ""
        google = " · Google connected" if connected else ""
        rows.append(
            f"<div class=row><span class='dot {level}'></span>"
            f"<span class=name>{html.escape(identity)}{role}"
            f"<div class=sub style='margin:0'>{detail}{google}</div></span></div>")

    if not rows:
        rows.append("<p class=sub style='margin:0'>Nobody is configured yet.</p>")

    error = (f"<div class=flash style='margin:.35rem 0 .2rem'>"
             f"{html.escape(flash_error)}</div>" if flash_error else "")

    return (
        f"<div class=card><b>Who lives here</b>"
        f"<div style='margin:.5rem 0 .2rem'>{''.join(rows)}</div>"
        f"<div style='margin:.8rem 0 0;padding-top:.7rem;"
        f"border-top:1px solid var(--card-border)'>"
        f"<b style='font-size:.95rem'>Invite someone</b>"
        f"<p class=sub style='margin:.3rem 0 .5rem'>Adds them and sends a "
        f"sign-in link. If there is no way to deliver it, the link is shown "
        f"here for you to pass on — it is single use and expires.</p>"
        f"<form method=post action=/admin/invite>"
        f"<input name=name placeholder='name, e.g. sam' autocomplete=off "
        f"style='padding:.4rem .5rem;margin:0 .4rem .4rem 0'>"
        f"<input name=email type=email placeholder='email (optional)' "
        f"autocomplete=off style='padding:.4rem .5rem;margin:0 .4rem .4rem 0'>"
        f"<button class=yes>Invite</button></form>{error}</div></div>")


def _field(setting, value: str, error: str) -> str:
    kind = "password" if setting.secret else "text"
    shown = "" if setting.secret else value
    note = ""
    if setting.secret:
        note = ("<span class=sub> \u2014 stored; leave blank to keep</span>"
                if value else "<span class=sub> \u2014 not set</span>")
    clear_box = ""
    if setting.secret and value:
        clear_box = (f"<label class=sub style='display:block;margin:.15rem 0 0'>"
                     f"<input type=checkbox name='clear_{html.escape(setting.key)}'"
                     f" value='1'> remove the stored value</label>")
    return (
        f"<label style='display:block;margin:.7rem 0 0'>"
        f"<b>{html.escape(setting.label)}</b>{note}"
        f"<input type={kind} name='{html.escape(setting.key)}' "
        f"value='{html.escape(shown)}' autocomplete=off "
        f"placeholder='{html.escape(setting.placeholder)}' "
        f"style='display:block;width:100%;margin:.25rem 0 .2rem;"
        f"padding:.4rem .5rem'>"
        + (f"<span class=sub>{html.escape(setting.hint)}</span>"
           f"<details style='margin:.15rem 0 0'>"
           f"<summary class=sub style='cursor:pointer;font-size:.8rem'>Why"
           f"</summary>"
           f"<div class=sub style='margin:.25rem 0 0'>"
           f"{html.escape(setting.help)}</div></details>"
           if setting.hint else
           f"<span class=sub>{html.escape(setting.help)}</span>")
        + clear_box
        + (f"<div class=flash style='margin:.35rem 0 0'>"
           f"{html.escape(error)}</div>" if error else "")
        + "</label>")


def render_settings_card(errors: dict, submitted: dict) -> str:
    sections = []
    for group in _portal.agentbox_settings.GROUPS:
        fields = "".join(
            _field(setting,
                   submitted.get(setting.key, _portal.SETTINGS.value(setting.key)),
                   errors.get(setting.key, ""))
            for setting in _portal.agentbox_settings.SETTINGS
            if setting.group == group)
        sections.append(f"<div class=card><b>{html.escape(group)}</b>{fields}</div>")
    return (f"<form method=post action=/admin/settings>{''.join(sections)}"
            f"<div style='margin:.8rem 0 0'><button class=yes>Save settings"
            f"</button></div></form>")


def render_household_card(error: str, submitted: str | None) -> str:
    current = (submitted if submitted is not None
               else "\n".join(_portal.HOUSEHOLD.controllable()))
    refused = ", ".join(_portal.agentbox_household.NEVER_CONTROLLABLE)
    return (
        f"<div class=card><b>What the assistant may control</b>"
        f"<p class=sub style='margin:.4rem 0 0'>One entity id per line. "
        f"Lights and switches work without listing them; this is for anything "
        f"else you want reachable. The {html.escape(refused)} domains are "
        f"refused by the bridge whatever this says, so naming one is rejected "
        f"here rather than accepted and quietly ignored.</p>"
        f"<form method=post action=/admin/household>"
        f"<textarea name=entities rows=6 autocomplete=off "
        f"style='display:block;width:100%;margin:.5rem 0 .2rem;"
        f"padding:.4rem .5rem;font-family:ui-monospace,monospace'>"
        f"{html.escape(current)}</textarea>"
        + (f"<div class=flash style='margin:.35rem 0 .2rem'>"
           f"{html.escape(error)}</div>" if error else "")
        + "<button class=yes style='margin:.5rem 0 0'>Save devices</button>"
        "</form></div>")


def render_admin(identity: str, flash: str,
                 origin: str = "email",
                 errors: dict | None = None,
                 submitted: dict | None = None,
                 household_error: str = "",
                 household_submitted: str | None = None,
                 invite_error: str = "",
                 new_invite_error: str = "") -> bytes:
    parts = ["<h1>Operations</h1>",
             "<p class=sub>Admin only. How the box is doing, who lives here, "
             "and how everything is set up.</p>"]
    if flash:
        parts.append(f"<div class=flash>{html.escape(flash)}</div>")
    parts.append(_portal.render_status_card())
    parts.append(_portal.render_proposals_card())
    parts.append(_portal.render_onboarding_card())
    parts.append(_portal.render_new_invite_card(new_invite_error))
    parts.append(_portal.render_people_card(invite_error))

    channels = []
    if _portal.SETTINGS.value("smtp_host"):
        channels.append(
            f"Email via {html.escape(_portal.SETTINGS.value('smtp_host'))} as "
            f"{html.escape(_portal.SETTINGS.value('smtp_user') or 'an unnamed account')}")
    reachable = sorted(_portal.discord_identities())
    if reachable:
        channels.append("Discord DM to " + ", ".join(html.escape(n) for n in reachable))
    if channels:
        body = "".join(f"<div style='padding:.15rem 0'>{c}</div>" for c in channels)
        note = ("<p class=sub style='margin:.5rem 0 0'>Both are attempted when "
                "both are configured. A link is bound to the browser that "
                "requested it, so it is useless to anyone who merely reads "
                "it \u2014 which is why a chat channel is a safe place to send "
                "one.</p>")
    else:
        body = ("<div style='padding:.15rem 0'>Nothing configured \u2014 links "
                "are minted and reach nobody.</div>")
        note = ("<p class=sub style='margin:.5rem 0 0'>Set "
                "a mail server below, or connect Discord on your Accounts "
                "page. Until one exists, an operator hands links over "
                "directly with "
                "<code>agentbox-portal link &lt;name&gt;</code>.</p>")
    parts.append(f"<div class=card><b>How sign-in links are delivered</b>"
                 f"<div class=sub style='margin:.4rem 0 0'>{body}</div>"
                 f"{note}</div>")
    parts.append(_portal.render_settings_card(errors or {}, submitted or {}))
    parts.append(_portal.render_household_card(household_error, household_submitted))

    return _portal.page("Operations \u2014 Agentbox",
                        _portal.chrome(identity, _portal.ADMIN, origin, "/admin", "".join(parts)))
