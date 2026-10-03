"""Messages shown after an action, keyed so a URL can never carry free text.

Loaded by cli/agentbox-portal, never imported on its own. Other portal
names are reached through `portal`, the portal module itself.
"""
from __future__ import annotations

import sys

# The portal that loaded this file, which registers it as "<portal>.<part>".
portal = sys.modules[__name__.rpartition(".")[0]]

__all__ = [
    "FLASHES",
    "origin_refusal",
    "flash_text",
]

# Messages shown after an action, looked up by key rather than taken from the
# URL. The text lives here in full, nothing from the URL is ever shown, and a
# long message cannot lengthen a URL.
FLASHES = {
    "memory_saved": "Memory saved.",
    "memory_saved_edited": "Memory saved, with your edit.",
    "memory_rejected": "Rejected.",
    "memory_forgotten": "Forgotten.",
    "filed_as_feedback": ("Filed as feedback. It is on the list to fix "
                          "properly, and was not saved as a memory."),
    "consent_received": ("Google consent received. An operator has to finish "
                         "it \u2014 authorisation codes expire in about ten "
                         "minutes, so ask now if you can."),
    "disconnect_requested": ("Disconnect requested. An operator will revoke "
                             "the credential and remove it from this box."),
    "unknown_connector": "Unknown connector.",
    "unknown_action": "Unknown action.",
    "google_not_configured": "Google sign-in is not configured on this box.",
    "agent_link_cannot_decide": ("This link was created by the assistant, so "
                                 "it cannot approve memories. Sign in from the "
                                 "email link to decide this one."),
    "agent_link_cannot_disconnect": ("This link was created by the assistant, "
                                     "so it cannot disconnect an account. Sign "
                                     "in from the email link to do that."),
    "agent_link_cannot_forget": ("This link was created by the assistant, so "
                                 "it cannot remove memories. Sign in from the "
                                 "email link to do that."),
    "memory_service_down": "The memory service did not respond. Nothing changed.",
    "not_yours": "That is not yours to decide.",
    "pairing_started": ("Send the code below to the Agentbox bot on Discord "
                        "to finish connecting."),
    "chat_unlinked": "Discord disconnected. Sign-in links will not go there.",
    "chat_link_cannot_decide": ("This link was opened in a different browser "
                                "from the one that asked for it, so it can "
                                "read but not decide. Request a link here and "
                                "open it without leaving this browser."),
    "chat_link_cannot_forget": ("This link was opened in a different browser "
                                "from the one that asked for it, so it can "
                                "read but not remove memories. Request a link "
                                "here and open it in this browser."),
    "chat_link_cannot_disconnect": ("This link was opened in a different "
                                    "browser from the one that asked for it. "
                                    "Moving where your sign-in links arrive "
                                    "needs a link opened in this browser."),
    "chat_link_cannot_pair": ("This link was opened in a different browser "
                              "from the one that asked for it. Connecting a "
                              "chat account decides where your sign-in links "
                              "go, so it needs a link opened in this browser."),
    "agent_link_cannot_pair": ("This link was created by the assistant, so it "
                               "cannot connect a chat account. Connecting one "
                               "decides where your sign-in links are "
                               "delivered. Sign in from a link you asked for "
                               "yourself to do it."),
    "admin_only": ("That is admin only. Your account can manage its own "
                   "memories and accounts, but not the household's settings."),
    "agent_link_cannot_read_ops": ("Operations is not visible from a link the "
                                   "assistant created \u2014 it lists who "
                                   "lives here and where their sign-in links "
                                   "go, and the assistant can read any link it "
                                   "sends. Ask the bot for one with `link`, or "
                                   "open a link you requested yourself."),
    "settings_saved": "Saved.",
    "invite_sent": "Invited. Their sign-in link is on its way.",
    "invite_created": "Invitation sent. They fill in a short form, then "
                      "it comes back here for you to finish.",
    "invite_undelivered": "Invitation created, but nothing is configured to "
                          "carry it. Its link is below \u2014 hand it over "
                          "directly.",
    "proposal_discarded": "Discarded. Nothing was sent.",
    "proposal_gone": "That draft is no longer there.",
    "proposal_name_taken": "Somebody by that name already lives here, "
                           "so that invitation was refused rather than "
                           "sent. Nothing changed.",
    "onboarding_requested": "Approved. Their accounts and their own "
                            "bridge are being set up now \u2014 this "
                            "takes a minute, and the list below will "
                            "empty when it is done.",
    "onboarding_unknown": "No invite is waiting with that id. It may "
                          "have been completed already.",
    "onboarding_duplicate": "Already approved \u2014 it is being set "
                            "up now.",
    "settings_unchanged": "Nothing to change \u2014 those are the current values.",
    "household_saved": "Saved. The bridge picks this up on its next call.",
    "agent_link_cannot_configure": ("This link was created by the assistant, "
                                    "so it cannot change settings. Sign in "
                                    "from a link you asked for yourself."),
    "google_did_not_complete": ("Google did not complete the connection. "
                                "Try again, or ask the operator to check the "
                                "portal log."),
    "backup_ok": "Backup snapshot created and verified.",
    "backup_failed": "Backup failed — inspect system journal.",
    "restore_ok": "Restore rehearsal verified: all 4 tiers extracted cleanly.",
    "restore_failed": "Restore rehearsal failed.",
}

def origin_refusal(origin: str, action: str) -> str:
    """Which message explains an action refused because of how this session
    signed in.

    Both origins are downgraded, for different reasons, and naming the wrong
    one sends a person to do something that will not help. A chat-delivered
    link is limited because it was opened in a different browser, not because
    the assistant made it.
    """
    prefix = "chat_link" if origin == portal.ORIGIN_CHAT else "agent_link"
    return f"{prefix}_cannot_{action}"

def flash_text(key: str) -> str:
    """Look up a message. An unknown key renders nothing rather than itself."""
    return FLASHES.get(key, "")
