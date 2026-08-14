"""Household settings that belong to the household, not to a config file.

Who is an admin, where sign-in links are sent, how mail goes out — these are
decisions the people living here make, and every one of them used to require
an operator editing a systemd unit and restarting a service. That is the
operator's job leaking into the household's, for no security benefit: none of
these values is safer for being in a unit file.

## Why a table rather than a page per setting

The portal had grown three hand-rolled JSON stores by the time this was
written — connector requests, chat pairings, rule approvals — each with its
own load, save, permissions and validation. A fourth and fifth would have been
copy-paste, and copy-paste is how the fail-open auth bug shipped in this
codebase: five near-identical things drifted apart.

So a setting is declared once, here, and the portal renders and writes it
generically. Adding one is a single entry in SETTINGS with a validator. There
is no per-setting UI code to forget to guard.

## Where the values live, and why not in the same place as everything else

`~/.local/state/agentbox/portal/settings.json`, mode 0600, owned by the
operator's user and mounted into no container.

That last part is the security property, not an accident. `admins` decides who
may approve a memory; `identity_emails` decides where a sign-in link is
delivered. If the assistant could write either, it could make itself an admin
or point somebody's link at a mailbox it reads. It has no route to this file:
no MCP tool reaches it, no container mounts it, and the portal that does write
it requires a session that a chat-delivered link cannot obtain.

Settings that a *container* must read cannot live here for exactly that
reason — see `household.py` for those, which go on the read-only policy mount
instead.

## Precedence

Stored value, then the legacy environment variable, then the default. The env
var is still honoured so a box configured before this existed keeps working
and can be migrated by saving the form once.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class InvalidSetting(ValueError):
    """A submitted value was rejected. The message is shown to the person.

    `key` says which setting was at fault, so a form can put the message
    against the right field. Validators raise without it — they are given a
    value, not a name — and `SettingsStore.save` fills it in, which keeps the
    knowledge of which field was being cleaned in the one place that has it.
    """

    def __init__(self, message: str, key: str = "") -> None:
        super().__init__(message)
        self.key = key


@dataclass(frozen=True)
class Setting:
    """One household setting: how to store it, show it, and check it.

    `clean` is the whole of the validation. It returns the value to store or
    raises InvalidSetting with a sentence a person can act on — never a
    coerced value, because silently accepting a near-miss is how a setting
    ends up meaning something nobody chose.
    """

    key: str
    label: str
    help: str
    clean: Callable[[str], str]
    env: str = ""
    placeholder: str = ""
    # Rendered as a password field and never echoed back into the form. The
    # value still reaches the page's HTML as a masked marker only.
    secret: bool = False
    # Shown but not editable by a member; some settings decide privilege.
    admin_only: bool = True
    default: str = ""
    group: str = "General"


# --- validators ----------------------------------------------------------------
#
# Each one is total: it returns a cleaned value or raises. None of them
# silently drops part of the input, because a list that quietly loses an entry
# is worse than a rejection — the person believes they configured something
# they did not.

NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HOSTNAME = re.compile(r"^[a-zA-Z0-9.-]+$")


def _pairs(raw: str) -> list[tuple[str, str]]:
    out = []
    for chunk in raw.replace("\n", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, sep, value = chunk.partition(":")
        if not sep:
            raise InvalidSetting(
                f"'{chunk}' should look like name:value, for example "
                f"alex:alex@example.com")
        out.append((name.strip(), value.strip()))
    return out


def clean_names(raw: str) -> str:
    """A comma-separated list of identity names."""
    names = [n.strip() for n in raw.replace("\n", ",").split(",") if n.strip()]
    for name in names:
        if not NAME.match(name):
            raise InvalidSetting(
                f"'{name}' is not an identity name — lowercase letters, "
                f"digits and hyphens, starting with a letter")
    return ",".join(names)


def clean_identity_emails(raw: str) -> str:
    pairs = _pairs(raw)
    for name, address in pairs:
        if not NAME.match(name):
            raise InvalidSetting(f"'{name}' is not an identity name")
        if not EMAIL.match(address):
            raise InvalidSetting(f"'{address}' does not look like an email address")
    return ",".join(f"{n}:{a}" for n, a in pairs)


def clean_host(raw: str) -> str:
    host = raw.strip()
    if host and not HOSTNAME.match(host):
        raise InvalidSetting(f"'{host}' is not a hostname")
    return host


def clean_port(raw: str) -> str:
    port = raw.strip()
    if not port:
        return ""
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise InvalidSetting(f"'{port}' is not a port number")
    return port


def clean_email_or_blank(raw: str) -> str:
    value = raw.strip()
    if value and not EMAIL.match(value):
        raise InvalidSetting(f"'{value}' does not look like an email address")
    return value


def clean_free(raw: str) -> str:
    return raw.strip()


# --- the settings themselves ---------------------------------------------------

SETTINGS: tuple[Setting, ...] = (
    Setting(
        key="admins",
        label="Admins",
        help="Who can see Operations and decide household memories. Everyone "
             "else manages only their own. Empty means nobody is an admin, "
             "which is the right direction for a mistake to fail in.",
        clean=clean_names,
        env="AGENTBOX_ADMINS",
        placeholder="alex",
        group="Who lives here",
    ),
    Setting(
        key="identity_emails",
        label="Sign-in addresses",
        help="Which address belongs to which person, so they can ask for their "
             "own sign-in link. An address here is not a credential — it only "
             "decides where a link is sent, and the link is limited unless "
             "opened in the browser that asked for it.",
        clean=clean_identity_emails,
        env="AGENTBOX_IDENTITY_EMAILS",
        placeholder="alex:alex@example.com, sam:sam@example.com",
        group="Who lives here",
    ),
    Setting(
        key="smtp_host",
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
        label="Password",
        help="For Gmail this is an app password, not the account password, and "
             "it needs 2-Step Verification switched on.",
        clean=clean_free,
        env="AGENTBOX_SMTP_PASSWORD",
        secret=True,
        group="Email delivery",
    ),
)

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
    # None means "read os.environ when asked". A snapshot taken at
    # construction would freeze the legacy fallback at import time, which is
    # both surprising and untestable — the store is built once at startup.
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

    def save(self, submitted: dict[str, str]) -> list[str]:
        """Validate and store. Returns the keys that actually changed.

        Every value is validated before anything is written, so a form with
        one bad field changes nothing rather than applying the good half — a
        half-applied settings page is how somebody ends up with an admin list
        they did not intend.
        """
        stored = self._stored()
        cleaned: dict[str, str] = {}
        for key, raw in submitted.items():
            setting = BY_KEY.get(key)
            if setting is None:
                continue
            if setting.secret and raw == "":
                continue        # left blank means "keep what is there"
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
