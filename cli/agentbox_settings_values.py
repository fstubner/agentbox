"""Validators for portal settings.

Each one returns a cleaned value or raises InvalidSetting with a reason. None
of them silently drops part of the input, because a list that quietly loses an
entry leaves the person believing they configured something they did not.
"""
from __future__ import annotations

import re

NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HOSTNAME = re.compile(r"^[a-zA-Z0-9.-]+$")
# Discord ids are snowflakes, decimal integers of currently 17 to 20 digits.
# Only the shape is checked. The approval loop ignores any id not on the
# operator list, so a mistyped id grants nothing, but it would quietly stop
# working for you, so the shape is checked here.
SNOWFLAKE = re.compile(r"^[0-9]{15,25}$")


class InvalidSetting(ValueError):
    """A submitted value was rejected. The message is shown to the person.

    `key` names the setting, so a form can show the message beside the right
    field. Validators only see a value, so `SettingsStore.save` fills it in.
    """

    def __init__(self, message: str, key: str = "") -> None:
        super().__init__(message)
        self.key = key


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


def clean_admins(raw: str) -> str:
    """Identity names, and never an empty list.

    A stored value beats the environment fallback, so saving `admins` blank
    would leave nobody an admin and Operations unreachable, with no way back
    through the page. An admin list may still be handed over, and any
    non-empty list saves, including one without the person saving it.

    A missing or garbled environment variable still means nobody is an admin,
    which is the safe direction for that kind of mistake.
    """
    names = clean_names(raw)
    if not names:
        raise InvalidSetting(
            "at least one admin is required — saving this empty would leave "
            "nobody able to reach Operations, including you, and it cannot be "
            "undone from this page. To hand over, name the new admin instead.")
    return names


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


def clean_snowflake(raw: str) -> str:
    value = raw.strip()
    if value and not SNOWFLAKE.match(value):
        raise InvalidSetting(
            f"'{value}' is not a Discord id. Turn on Developer Mode in "
            f"Discord, then right-click and Copy ID — it is a long number, "
            f"not a username")
    return value


def clean_snowflakes(raw: str) -> str:
    ids = [i.strip() for i in raw.replace("\n", ",").split(",") if i.strip()]
    for value in ids:
        clean_snowflake(value)
    return ",".join(ids)
