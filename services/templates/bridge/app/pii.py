"""Detect personal data in text, replace it with placeholders, and put it back.

## Why placeholders rather than deletion

Deleting an email address stops the assistant reading it and also stops it
replying to anyone. Useless privacy is privacy that gets switched off.

So personal data is *substituted*: the model sees `<EMAIL_1>` where an address
was, reasons about "the sender" perfectly well, and writes a draft addressed to
`<EMAIL_1>`. The bridge puts the real address back on the way out. The value
never enters the model's context and the workflow still works.

That also means this is not a filter the model can be talked into bypassing —
it cannot reveal what it was never given.

## What this reliably catches, and what it does not

Structured identifiers with a checkable shape: email addresses, phone numbers,
payment cards (Luhn-checked), IBANs (mod-97 checked), UK National Insurance
numbers, US Social Security numbers, IP addresses, UK postcodes, and
credential-shaped strings.

It does **not** reliably catch names, street addresses, dates of birth in
prose, or medical detail. Those have no checkable shape, and a regex claiming
to find them produces both misses and false positives on ordinary words. A
system that says "PII removed" while leaving "Sam's checkup result was clear" in
place is worse than one that never claimed it, because someone relies on it.

Treat this as reducing exposure of identifiers, not as anonymisation.

## Precision over recall, deliberately

Every pattern here is checksum-validated where the format allows. A false
positive is not harmless: mangling an order number into `<CARD_1>` corrupts
data the assistant is meant to act on, silently, and the person who notices is
the one whose delivery never arrived.
"""
from __future__ import annotations

import re

# Ordered: earlier patterns win a contested span, so the more specific and
# checksum-backed kinds are matched before the looser ones.
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("NINO", re.compile(
        r"\b[ABCEGHJKLMNOPRSTWXYZ][ABCEGHJKLMNPRSTWXYZ]\s?"
        r"\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b")),
    ("SSN", re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
    ("PHONE", re.compile(
        r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?"
        r"\d{3,5}[\s.-]?\d{3,4}[\s.-]?\d{0,4}(?![\w.])")),
    ("POSTCODE", re.compile(
        r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b", re.I)),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("SECRET", re.compile(
        r"\b(?:sk-|pk_|ghp_|gho_|xox[baprs]-|AKIA)[A-Za-z0-9_\-]{10,}\b")),
]

# Kinds carrying a checksum. Anything matching the shape but failing the check
# is left alone, because it was probably never the thing.
CHECKED = {"CARD", "IBAN"}


def _luhn_ok(digits: str) -> bool:
    """Payment-card check digit.

    Without this, every 16-digit order number and tracking code becomes
    `<CARD_1>` — data the assistant needed, destroyed silently.
    """
    total, alternate = 0, False
    for char in reversed(digits):
        value = ord(char) - 48
        if alternate:
            value *= 2
            if value > 9:
                value -= 9
        total += value
        alternate = not alternate
    return total % 10 == 0


def _iban_ok(value: str) -> bool:
    """IBAN mod-97, per ISO 13616."""
    rotated = value[4:] + value[:4]
    numeric = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rotated)
    try:
        return int(numeric) % 97 == 1
    except ValueError:
        return False


def _valid(kind: str, text: str) -> bool:
    if kind == "CARD":
        digits = re.sub(r"[ -]", "", text)
        return 13 <= len(digits) <= 19 and _luhn_ok(digits)
    if kind == "IBAN":
        return _iban_ok(text.upper())
    if kind == "PHONE":
        # Guard against swallowing ordinary numbers: require enough digits to
        # actually be a phone number, and reject runs that are clearly a year,
        # a price, or an id.
        digits = re.sub(r"\D", "", text)
        return 9 <= len(digits) <= 15
    if kind == "IP":
        return all(part.isdigit() and int(part) < 256
                   for part in text.split("."))
    return True


# Kinds the assistant has no legitimate use for.
#
# It does not need a card number to summarise a receipt, or a National
# Insurance number to file a letter. Because nothing downstream ever needs the
# value back, these can be removed outright — no placeholder to restore, no
# mapping to keep, no state to expire. That is what makes this half shippable
# on its own: the hard part of substitution is remembering what was replaced,
# and here there is nothing to remember.
#
# Emails and phone numbers are deliberately absent. The assistant needs those
# to reply to anyone, so they require the reversible path and are handled by
# Redactor, not here.
NEVER_NEEDED = ("CARD", "IBAN", "NINO", "SSN", "SECRET")


def strip_sensitive(text: str, kinds: tuple[str, ...] = NEVER_NEEDED) -> str:
    """Remove high-harm identifiers outright, before the model sees them.

    Irreversible on purpose. A value the assistant is never given is a value it
    cannot leak, be talked into repeating, or write into a memory proposal —
    and unlike a filter on the way out, there is no prompt that recovers it.
    """
    if not text:
        return text
    lookup = dict(PATTERNS)
    for kind in kinds:
        pattern = lookup.get(kind)
        if pattern is None:
            continue
        text = pattern.sub(
            lambda m, k=kind: f"[{k} removed]" if _valid(k, m.group())
            else m.group(), text)
    return text


class Redactor:
    """Substitutes personal data for placeholders, and can reverse it.

    One instance per conversation-ish scope. The mapping is stable within an
    instance, so the same address is always `<EMAIL_1>` — the model can tell
    two mentions apart, or recognise them as the same person, without ever
    being told who that person is.
    """

    def __init__(self) -> None:
        self._to_placeholder: dict[str, str] = {}
        self._to_value: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def _placeholder_for(self, kind: str, value: str) -> str:
        existing = self._to_placeholder.get(value)
        if existing:
            return existing
        self._counts[kind] = self._counts.get(kind, 0) + 1
        placeholder = f"<{kind}_{self._counts[kind]}>"
        self._to_placeholder[value] = placeholder
        self._to_value[placeholder] = value
        return placeholder

    def redact(self, text: str) -> str:
        """Replace detected personal data with stable placeholders."""
        if not text:
            return text
        spans: list[tuple[int, int, str, str]] = []
        taken: list[tuple[int, int]] = []
        for kind, pattern in PATTERNS:
            for match in pattern.finditer(text):
                start, end = match.span()
                value = match.group()
                if not value.strip():
                    continue
                if not _valid(kind, value):
                    continue
                # An earlier, more specific kind already owns this text.
                if any(start < e and s < end for s, e in taken):
                    continue
                taken.append((start, end))
                spans.append((start, end, kind, value))
        out, cursor = [], 0
        for start, end, kind, value in sorted(spans):
            out.append(text[cursor:start])
            out.append(self._placeholder_for(kind, value))
            cursor = end
        out.append(text[cursor:])
        return "".join(out)

    def restore(self, text: str) -> str:
        """Put the real values back.

        Applied to what the assistant produces — a draft body, a search query —
        so a reply addressed to `<EMAIL_1>` reaches an actual person.
        """
        if not text:
            return text
        def swap(match):
            return self._to_value.get(match.group(), match.group())
        return re.sub(r"<[A-Z]+_\d+>", swap, text)

    def summary(self) -> dict[str, int]:
        """How much was replaced, by kind. For telling a person what happened
        without showing them the thing that was hidden."""
        counts: dict[str, int] = {}
        for placeholder in self._to_value:
            kind = placeholder[1:placeholder.rindex("_")]
            counts[kind] = counts.get(kind, 0) + 1
        return counts
