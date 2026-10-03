"""Validators for portal settings: each turns typed text into a clean value or refuses it with a reason."""
from __future__ import annotations

import re

NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HOSTNAME = re.compile(r"^[a-zA-Z0-9.-]+$")
# Discord ids are snowflakes: decimal integers, currently 17-20 digits. Checked
# for shape only. The real check is that the approval loop refuses to accept
# instructions from an id that is not on the operator list, so a mistyped id
# grants nothing to anybody — it just quietly stops working for you, which is
# the safe direction but a confusing one, hence validating what we can here.
SNOWFLAKE = re.compile(r"^[0-9]{15,25}$")


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

    `admins` is the one setting that can make itself uneditable. Saving it
    blank stores an empty string, and a stored value beats the environment
    fallback by design — so the box goes from "alex is an admin" to nobody
    is, Operations becomes unreachable for everyone, and the only way back is
    hand-editing settings.json as the operator. There is no confirmation step
    in front of it and no route through the UI to undo it.

    "Empty means nobody is an admin, which is the right direction for a
    mistake to fail in" is still true of the value being *absent* — a garbled
    or missing environment variable removes privilege rather than granting
    it. It is not a reason to let a form abolish administration of the box.
    Handing over is still allowed: any non-empty list saves, including one
    that does not contain the person saving it.
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
