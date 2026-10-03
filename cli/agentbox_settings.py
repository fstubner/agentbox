"""Household settings that belong to the household, not to a config file.

Who is an admin, where sign-in links go and how mail is sent are decisions for
the people who live here, so they are edited on the portal rather than in a
systemd unit. None of these values is safer for being in a unit file.

## One table rather than a page per setting

A setting is declared once, in SETTINGS, with a validator, and the portal
renders and saves it generically. There is no per-setting page code to forget
to guard.

## Where the values live

`~/.local/state/agentbox/portal/settings.json`, mode 600, owned by the
operator and mounted into no container. That is the security property.
`admins` decides who may approve a memory, and `identity_emails` decides where
a sign-in link goes. If the assistant could write either, it could make itself
an admin or redirect someone's link to a mailbox it reads. No tool reaches this
file, no container mounts it, and the portal only writes it for a session that
a chat-delivered link cannot get.

Settings a container must read go on the read-only policy mount instead. See
`agentbox_household.py`.

## Precedence

The stored value, then the old environment variable, then the default. The
environment variable is still read, so an older box keeps working until the
form is saved once.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentbox_settings_values import (  # noqa: F401
    InvalidSetting,
    _pairs,
    clean_admins,
    clean_email_or_blank,
    clean_free,
    clean_host,
    clean_identity_emails,
    clean_names,
    clean_port,
    clean_snowflake,
    clean_snowflakes,
)


@dataclass(frozen=True)
class Setting:
    """One household setting: how to store it, show it and check it.

    `clean` is all of the validation. It returns the value to store, or raises
    InvalidSetting with a sentence a person can act on. It never coerces a
    near miss into something nobody chose.
    """

    key: str
    label: str
    # The reasoning behind the setting, folded away under "Why" so an admin
    # adding someone does not have to read it first.
    help: str
    clean: Callable[[str], str]
    # One line, shown next to the field. When empty the long form is shown
    # inline instead, so a setting added without a hint is still explained.
    hint: str = ""
    env: str = ""
    placeholder: str = ""
    # Rendered as a password field and never echoed back into the form. The
    # value still reaches the page's HTML as a masked marker only.
    secret: bool = False
    # Shown but not editable by a member; some settings decide privilege.
    admin_only: bool = True
    default: str = ""
    group: str = "General"


# --- the settings themselves ---------------------------------------------------

SETTINGS: tuple[Setting, ...] = (
    Setting(
        key="admins",
        hint="Who can see Operations and decide household memories.",
        label="Admins",
        help="Who can see Operations and decide household memories. Everyone "
             "else manages only their own. At least one name is required: "
             "saving this empty would leave nobody able to reach this page, "
             "and there is no way back from that except editing a file on the "
             "box. Handing over is fine \u2014 name the new admin.",
        clean=clean_admins,
        env="AGENTBOX_ADMINS",
        placeholder="alex",
        group="Roles and addresses",
    ),
    Setting(
        key="identity_emails",
        hint="Which address belongs to which person.",
        label="Sign-in addresses",
        help="Which address belongs to which person, so they can ask for their "
             "own sign-in link. An address here is not a credential — it only "
             "decides where a link is sent, and the link is limited unless "
             "opened in the browser that asked for it.",
        clean=clean_identity_emails,
        env="AGENTBOX_IDENTITY_EMAILS",
        placeholder="alex:alex@example.com, sam:sam@example.com",
        group="Roles and addresses",
    ),
    Setting(
        key="smtp_host",
        hint="Leave blank to send no email. Discord needs none of this.",
        label="Mail server",
        help="Leave blank to send no email at all. Discord pairing needs none "
             "of this.",
        clean=clean_host,
        env="AGENTBOX_SMTP_HOST",
        placeholder="smtp.gmail.com",
        group="Email delivery",
    ),
    Setting(
        key="smtp_port",
        hint="587 for STARTTLS.",
        label="Port",
        clean=clean_port,
        env="AGENTBOX_SMTP_PORT",
        help="587 for STARTTLS, which is what this uses.",
        placeholder="587",
        default="587",
        group="Email delivery",
    ),
    Setting(
        key="smtp_user",
        hint="Mail is sent as this address.",
        label="Account",
        help="Mail is sent as this address, so it is the name people will see "
             "the link come from.",
        clean=clean_email_or_blank,
        env="AGENTBOX_SMTP_USER",
        placeholder="agentbox@example.com",
        group="Email delivery",
    ),
    Setting(
        key="smtp_password",
        hint="For Gmail, an app password — not the account password.",
        label="Password",
        help="For Gmail this is an app password, not the account password, and "
             "it needs 2-Step Verification switched on.",
        clean=clean_free,
        env="AGENTBOX_SMTP_PASSWORD",
        secret=True,
        group="Email delivery",
    ),
    Setting(
        key="not_deployed",
        label="Services this household does not run",
        hint="Named here, they are listed but never counted as broken.",
        help="A compose directory with no container could mean two things — "
             "never deployed here, or deployed and since removed — and "
             "nothing on the box can tell them apart. Guessing either way is "
             "wrong: guess 'never' and `docker compose down` reads as "
             "healthy; guess 'removed' and the page is permanently red over "
             "something you chose not to run. So it is stated rather than "
             "inferred. Anything absent and not named here is treated as a "
             "fault.",
        clean=clean_names,
        env="AGENTBOX_NOT_DEPLOYED",
        placeholder="eufy-bridge",
        group="Roles and addresses",
    ),
    Setting(
        key="approval_channel",
        hint="Where the assistant asks when a tool needs permission.",
        label="Approval channel",
        help="The Discord channel the assistant asks in when a tool needs "
             "permission. Blank stops the approval loop from starting at all, "
             "which is the right failure: a loop with nowhere to ask would "
             "otherwise sit there looking healthy.",
        clean=clean_snowflake,
        env="AGENTBOX_APPROVAL_CHANNEL_ID",
        placeholder="100000000000000001",
        group="Discord",
    ),
    Setting(
        key="approval_user_ids",
        hint="Whose replies the approval loop acts on.",
        label="Who may approve",
        help="Discord ids whose replies the approval loop will act on. "
             "Everyone else is ignored, including the assistant — bot "
             "messages are skipped before this list is consulted, so an "
             "injected instruction cannot approve itself.",
        clean=clean_snowflakes,
        env="AGENTBOX_APPROVAL_USER_IDS",
        placeholder="221334455667788990",
        group="Discord",
    ),
)

# The Discord bot token is not here. It stays in the secret manager, which
# handles rotation and auditing. This table moves configuration out of local
# files and leaves credentials alone.

BY_KEY = {setting.key: setting for setting in SETTINGS}
GROUPS = tuple(dict.fromkeys(setting.group for setting in SETTINGS))


# --- storage -------------------------------------------------------------------


@dataclass
class SettingsStore:
    """Reads and writes the settings file. One instance per process is enough.

    Deliberately not cached: the portal is a threaded HTTP server and the file
    is small, so re-reading is cheaper than reasoning about invalidation after
    a write.
    """

    directory: Path
    # None means read os.environ when asked, rather than freezing the fallback
    # when the store is built at startup.
    environ: dict | None = None

    def _env(self) -> dict:
        return os.environ if self.environ is None else self.environ

    @property
    def path(self) -> Path:
        return self.directory / "settings.json"

    def _stored(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def value(self, key: str) -> str:
        """Stored, then the legacy environment variable, then the default."""
        setting = BY_KEY[key]
        stored = self._stored()
        if key in stored:
            return str(stored[key])
        env = self._env()
        if setting.env and env.get(setting.env):
            return str(env[setting.env])
        return setting.default

    def all(self) -> dict[str, str]:
        return {setting.key: self.value(setting.key) for setting in SETTINGS}

    def save(self, submitted: dict[str, str],
             clear: frozenset[str] = frozenset()) -> list[str]:
        """Validate and store. Returns the keys that changed.

        Every value is checked before anything is written, so a form with one
        bad field changes nothing, rather than applying half of it.
        """
        stored = self._stored()
        cleaned: dict[str, str] = {}
        for key, raw in submitted.items():
            setting = BY_KEY.get(key)
            if setting is None:
                continue
            if setting.secret and raw == "" and key not in clear:
                # Blank means keep what is there, because a password field is
                # never shown back and every save would otherwise wipe it.
                # `clear` is how a caller says it really meant empty.
                continue
            try:
                cleaned[key] = setting.clean(raw)
            except InvalidSetting as exc:
                raise InvalidSetting(str(exc), key=key) from exc

        changed = [k for k, v in cleaned.items() if stored.get(k) != v]
        if not changed:
            return []
        stored.update(cleaned)
        self.directory.mkdir(parents=True, exist_ok=True)
        # Written whole then moved, so a crash mid-write cannot leave a
        # truncated file that reads as "nobody is an admin".
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(stored, indent=2, sort_keys=True),
                       encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.path)
        return changed
