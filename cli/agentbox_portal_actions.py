"""Admin and invite actions, mixed into PortalHandler.

Loaded by cli/agentbox-portal, never imported on its own. Other portal
names are reached through `portal`, the portal module itself.
"""
from __future__ import annotations

import hmac
import html
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

# The portal that loaded this file, which registers it as "<portal>.<part>".
portal = sys.modules[__name__.rpartition(".")[0]]

__all__ = [
    "PortalActions",
]


class PortalActions:
    """Admin and invite actions, mixed into PortalHandler."""

    def _invite(self, session, form) -> None:
        """Add a person and send them a sign-in link.

        Creating an identity grants access, so this is withheld from agent and
        chat links like editing the admin list. If the assistant could invite
        people it could decide who lives here.

        The link is ORIGIN_CHAT, not ORIGIN_OPERATOR. The CLI prints a link for
        a person to hand over, but this one is sent by email or Discord DM,
        channels the assistant can read. With no browser nonce to bind it, an
        operator-level link would sit in a mailbox the assistant can search,
        able to approve memories and disconnect accounts. Someone who only
        reads a link must not be able to use it, as the module docstring says.
        agentbox-approvals mints ORIGIN_CHAT for the same reason.

        The invitee still has what they need on arrival. They can read
        everything, and request a browser-bound link for anything more.
        """
        why = portal.refusal(session["role"], "ops:invite",
                      session.get("origin", portal.ORIGIN_AGENT))
        if why:
            self._redirect(f"/admin?m={why}")
            return

        name = (form.get("name") or [""])[0].strip()
        address = (form.get("email") or [""])[0].strip()

        def fail(message: str) -> None:
            self._send(400, portal.render_admin(
                session["identity"], "", session.get("origin", portal.ORIGIN_AGENT),
                invite_error=message))

        try:
            name = portal.agentbox_settings.clean_names(name)
            address = portal.agentbox_settings.clean_email_or_blank(address)
        except portal.agentbox_settings.InvalidSetting as exc:
            return fail(str(exc))
        if not name or "," in name:
            return fail("Give one name, for example sam.")

        # Append to the map instead of replacing it. Saving the whole map from
        # a two-field form would drop everybody not in the form.
        if address:
            existing = portal.email_map()
            existing = {a: n for a, n in existing.items() if n != name}
            existing[address.lower()] = name
            pairs = ",".join(f"{n}:{a}" for a, n in sorted(existing.items()))
            try:
                portal.SETTINGS.save({"identity_emails": pairs})
            except portal.agentbox_settings.InvalidSetting as exc:
                return fail(str(exc))

        # Downgrade only links that are sent. A link shown to the admin to
        # hand over in person keeps operator privilege, so on a box with no
        # delivery channel a new member can still approve their first memory.
        travels = portal.has_delivery_channel(name, address)
        try:
            url, _ = portal.mint_link(
                name, portal.PUBLIC_URL,
                origin=portal.ORIGIN_CHAT if travels else portal.ORIGIN_OPERATOR)
        except RuntimeError as exc:
            return fail(str(exc))

        if portal.deliver_link(name, address, url):
            self._redirect("/admin?m=invite_sent")
            return
        # Nowhere to send it, so show the admin the link to hand over. This is
        # the same as the terminal command, with the same origin, because a
        # link handed over by a person has not passed through a channel
        # anything else can read.
        self._send(200, portal.render_admin(
            session["identity"], "", session.get("origin", portal.ORIGIN_AGENT),
            invite_error=f"{name} has no way to receive a link yet. Hand this "
                         f"over directly. Single use, and it expires: {url}"))


    def _propose_invite(self, form) -> None:
        """Let the assistant draft an invitation. Sends nothing.

        This has a weaker guard than _agent_link. _agent_link takes no
        identity, because naming a person is what an injected instruction
        would do. An invitation has to name somebody, so that guard cannot
        apply here.

        It is safe because it only writes a draft. The draft appears on
        Operations for a person to decide on. An email saying "add
        alex@example.com to your assistant" produces a draft an admin reads
        and rejects. No account is created.
        """
        provided = self.headers.get("X-Agentbox-Portal-Token", "")
        if not portal.AGENT_TOKEN or not provided or not hmac.compare_digest(
                provided, portal.AGENT_TOKEN):
            self._send(403, portal.page("No", "<h1>No</h1>"))
            return
        # Named `account_name`, not `identity`, because it names an account
        # that does not exist yet. /agent/link takes an `identity` to act as,
        # so that word has a specific meaning in this module.
        identity = (form.get("account_name") or [""])[0].strip().lower()
        if not portal.agentbox_onboarding.IDENTITY_NAME.match(identity):
            self._send(400, portal.page("No", "<h1>a name is required</h1>"))
            return
        address = (form.get("email") or [""])[0].strip()
        user_id = (form.get("discord_user_id") or [""])[0].strip()
        if not address and not user_id:
            self._send(400, portal.page(
                "No", "<h1>an email address or a Discord id is required</h1>"))
            return
        # Refused with an error, not queued. A flood of drafts gains no
        # privilege, but it can make an admin stop reading them. The admin
        # reading each draft is the safeguard.
        if len(portal.agentbox_onboarding.proposals()) >= portal.MAX_PENDING_PROPOSALS:
            self._send(429, portal.page(
                "Slow down",
                f"<h1>{portal.MAX_PENDING_PROPOSALS} invitations are already waiting "
                f"for the household to decide on.</h1>"))
            return
        draft = portal.agentbox_onboarding.propose(
            identity, (form.get("display_name") or [""])[0],
            address, user_id, proposed_by="assistant")
        body = json.dumps({
            "drafted": True,
            "id": draft["id"],
            "note": "Nothing has been sent. It is waiting for an admin on the "
                    "Operations page.",
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _create_invite(self, session, form) -> None:
        """Create an invite and send whoever it names the form link.

        Withheld from downgraded origins under `ops:invite`, like every other
        way of deciding who lives here. It matters more than for drafts,
        because this produces the credential.

        Completing an invite re-provisions that identity's bridge with whoever
        filled in the form, so an invite naming someone who already lives here
        would hand over their account. `create_invite` requires the existing
        names so this check cannot be forgotten.
        """
        why = portal.refusal(session["role"], "ops:invite",
                      session.get("origin", portal.ORIGIN_AGENT))
        if why:
            self._redirect(f"/admin?m={why}")
            return

        def fail(message: str) -> None:
            self._send(200, portal.render_admin(
                session["identity"], "", session.get("origin", portal.ORIGIN_AGENT),
                new_invite_error=message))

        name = (form.get("account_name") or [""])[0].strip().lower()
        address = (form.get("email") or [""])[0].strip()
        user_id = (form.get("discord_user_id") or [""])[0].strip()
        try:
            record = portal.agentbox_onboarding.create_invite(
                name, portal.known_identities())
        except portal.agentbox_onboarding.NameTaken:
            return fail(f"Somebody called {html.escape(name)} already lives "
                        f"here. Inviting them to that name would hand over "
                        f"the account rather than create one.")
        except portal.agentbox_onboarding.AlreadyInvited:
            return fail(f"{html.escape(name)} already has an invite waiting, "
                        f"and its link is below. A second one would be a "
                        f"second credential for the same person.")
        except ValueError as exc:
            return fail(html.escape(str(exc)))

        url = portal.agentbox_onboarding.invite_url(
            record, portal.invite_public_host(), portal.INVITE_PORT)
        channels = portal.deliver_invite(
            {"identity": name, "display_name": name, "address": address,
             "discord_user_id": user_id}, url)
        # Always redirect, even with nothing to deliver by, so a refresh
        # cannot resubmit and make another live link. The card below lists every
        # outstanding invite and its link, so nothing is lost.
        self._redirect("/admin?m="
                       + ("invite_created" if channels else "invite_undelivered"))

    def _proposal(self, session, form) -> None:
        """Send or discard an invitation the assistant drafted.

        Withheld from downgraded origins under `ops:invite`. Otherwise the
        assistant could make itself a link, open its own draft and approve it.
        """
        why = portal.refusal(session["role"], "ops:invite",
                      session.get("origin", portal.ORIGIN_AGENT))
        if why:
            self._redirect(f"/admin?m={why}")
            return

        draft = portal.agentbox_onboarding.proposal(
            (form.get("id") or [""])[0].strip())
        if not draft:
            self._redirect("/admin?m=proposal_gone")
            return
        if (form.get("action") or [""])[0] == "discard":
            portal.agentbox_onboarding.discard_proposal(draft)
            self._redirect("/admin?m=proposal_discarded")
            return

        try:
            record = portal.agentbox_onboarding.create_invite(
                str(draft.get("identity", "")), portal.known_identities())
        except portal.agentbox_onboarding.NameTaken:
            # Left in place, not discarded, so the admin can still see what was
            # asked for.
            self._redirect("/admin?m=proposal_name_taken")
            return
        except ValueError:
            self._redirect("/admin?m=proposal_gone")
            return

        url = portal.agentbox_onboarding.invite_url(
            record, portal.invite_public_host(), portal.INVITE_PORT)
        channels = portal.deliver_invite(draft, url)
        portal.agentbox_onboarding.discard_proposal(draft)
        if channels:
            self._redirect("/admin?m=" + urllib.parse.quote(
                f"Invitation sent by {' and '.join(channels)}."))
            return
        # Nowhere to send it. Use the same fallback as the invite form, and do
        # not report a delivery that did not happen.
        self._send(200, portal.render_admin(
            session["identity"], "", session.get("origin", portal.ORIGIN_AGENT),
            invite_error=f"Nothing is configured to carry that. Hand this "
                         f"over directly \u2014 single use, and it expires: "
                         f"{url}"))

    def _onboard(self, session, form) -> None:
        """Ask for a filled-in invite to be completed.

        Withheld from agent and chat links under the same capability as
        inviting, because this finishes that act and hands the person their
        own credential.

        This writes one file and returns. Whether the invite can be completed
        is for `agentbox invite drain` to decide, since it re-reads the record.
        A second copy of those rules here would drift. The checks below only
        make the page report accurately what it did.
        """
        why = portal.refusal(session["role"], "ops:invite",
                      session.get("origin", portal.ORIGIN_AGENT))
        if why:
            self._redirect(f"/admin?m={why}")
            return

        token_id = (form.get("token_id") or [""])[0].strip()
        # Comes from a form and will become a filename, so a malformed id is
        # refused here as a redirect rather than an exception in the handler.
        if not portal.agentbox_onboarding.TOKEN_ID.match(token_id):
            self._redirect("/admin?m=onboarding_unknown")
            return
        if not any(i["id"] == token_id for i in portal.submitted_invites()):
            self._redirect("/admin?m=onboarding_unknown")
            return
        if portal.agentbox_onboarding.already_requested(token_id):
            self._redirect("/admin?m=onboarding_duplicate")
            return
        try:
            portal.agentbox_onboarding.request(
                token_id, session["identity"],
                session.get("origin", portal.ORIGIN_AGENT))
        except (OSError, ValueError):
            # Do not report the request as filed when it was not. The invite
            # path does the same for delivery.
            self._redirect("/admin?m=onboarding_unknown")
            return
        self._redirect("/admin?m=onboarding_requested")

    def _save_settings(self, session, form) -> None:
        """Write the household settings, or re-render the form with errors.

        Errors are rendered from this POST rather than carried through a
        redirect, because a validation message quotes what the person typed.
        Flash messages travel in the URL as *keys* into a fixed table, so no
        one can craft a link that shows another admin an arbitrary sentence.
        Echoing input through `?m=` would remove that protection. Re-rendering
        also keeps the rest of the form filled in.
        """
        why = portal.refusal(session["role"], "ops:write_settings",
                      session.get("origin", portal.ORIGIN_AGENT))
        if why:
            self._redirect(f"/admin?m={why}")
            return
        submitted = {setting.key: (form.get(setting.key) or [""])[0]
                     for setting in portal.agentbox_settings.SETTINGS}
        errors: dict[str, str] = {}
        clear = frozenset(
            setting.key for setting in portal.agentbox_settings.SETTINGS
            if setting.secret and (form.get(f"clear_{setting.key}") or [""])[0])
        # `clean_admins` refuses an empty list, but it only checks shape, so a
        # misspelt name would pass and also lock everyone out.
        # Every admin named must be someone who exists.
        wanted = {n.strip() for n in submitted.get("admins", "").split(",")
                  if n.strip()}
        unknown = sorted(wanted - portal.known_identities(also=submitted))
        if unknown:
            self._send(400, portal.render_admin(
                session["identity"], "", session.get("origin", portal.ORIGIN_AGENT),
                errors={"admins": (
                    f"nobody here is called {', '.join(unknown)}. Invite them "
                    f"first, or check the spelling. An admin list naming "
                    f"only people who do not exist locks this page for "
                    f"everyone, and cannot be undone from it.")},
                submitted=submitted))
            return
        try:
            changed = portal.SETTINGS.save(submitted, clear=clear)
        except portal.agentbox_settings.InvalidSetting as exc:
            # The store validates everything before writing, so a rejection
            # means nothing was stored and the form shows what was typed.
            errors[exc.key] = str(exc)
            self._send(400, portal.render_admin(
                session["identity"], "", session.get("origin", portal.ORIGIN_AGENT),
                errors=errors, submitted=submitted))
            return
        self._redirect("/admin?m="
                       + ("settings_saved" if changed else "settings_unchanged"))

    def _save_household(self, session, form) -> None:
        why = portal.refusal(session["role"], "ops:write_household",
                      session.get("origin", portal.ORIGIN_AGENT))
        if why:
            self._redirect(f"/admin?m={why}")
            return
        raw = (form.get("entities") or [""])[0]
        try:
            portal.HOUSEHOLD.save(raw)
        except portal.agentbox_household.InvalidEntity as exc:
            self._send(400, portal.render_admin(
                session["identity"], "", session.get("origin", portal.ORIGIN_AGENT),
                household_error=str(exc), household_submitted=raw))
            return
        self._redirect("/admin?m=household_saved")
