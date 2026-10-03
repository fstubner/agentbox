"""Personal-data substitution.

Two failure directions matter and they pull against each other. Missing real
personal data defeats the point. Mangling an order number into <CARD_1>
corrupts data the assistant was meant to act on, silently, and the person who
finds out is the one whose delivery never arrived.

So: checksum-validated where the format allows, and honest about the kinds
that have no checkable shape at all.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "services" / "templates" / "bridge" / "app" / "pii.py"


@pytest.fixture
def pii():
    spec = importlib.util.spec_from_file_location("pii", MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pii"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def red(pii):
    return pii.Redactor()


# --- detection -----------------------------------------------------------------


def test_email_is_replaced(red):
    out = red.redact("Write to sam@example.com about it")
    assert "sam@example.com" not in out
    assert "<EMAIL_1>" in out


def test_the_same_value_keeps_the_same_placeholder(red):
    """The assistant must be able to tell two mentions are one person without
    being told who that person is."""
    out = red.redact("sam@example.com replied to sam@example.com")
    assert out.count("<EMAIL_1>") == 2
    assert "<EMAIL_2>" not in out


def test_different_values_are_distinguishable(red):
    out = red.redact("from sam@example.com to alex@example.com")
    assert "<EMAIL_1>" in out and "<EMAIL_2>" in out


def test_real_card_number_is_caught(red):
    # A Luhn-valid test number, not anyone's card.
    out = red.redact("card 4111 1111 1111 1111 on file")
    assert "4111" not in out
    assert "<CARD_1>" in out


def test_order_number_that_looks_like_a_card_is_left_alone(red):
    """The false-positive direction. A 16-digit order number mangled into
    <CARD_1> is data destroyed silently."""
    out = red.redact("order 1234567812345678 shipped")
    assert "1234567812345678" in out
    assert "CARD" not in out


def test_iban_checksum_is_enforced(red):
    valid = red.redact("pay GB82WEST12345698765432 today")
    assert "<IBAN_1>" in valid
    invalid = pytest_redactor().redact("ref GB99WEST12345698765432 today")
    assert "GB99WEST12345698765432" in invalid


def test_national_insurance_and_ssn(red):
    out = red.redact("NI AB123456C and SSN 123-45-6789")
    assert "AB123456C" not in out and "123-45-6789" not in out
    assert "<NINO_1>" in out and "<SSN_1>" in out


def test_impossible_ni_prefixes_are_not_matched(red):
    """Q is not a legal first or second letter of a National Insurance number.

    Found by getting it wrong in a test: the invented example QQ123456C was
    never a real format, and the pattern was right to decline it.
    """
    text = "reference QQ123456C on the form"
    assert red.redact(text) == text


def test_secrets_are_caught(red):
    out = red.redact("token sk-abcdefghijklmnop and ghp_ABCDEFGHIJKLMNOP")
    assert "sk-abcdefghijklmnop" not in out
    assert "ghp_ABCDEFGHIJKLMNOP" not in out


def test_short_numbers_are_not_phone_numbers(red):
    """Prices, years and quantities must survive untouched."""
    text = "3 items, 2026 budget of 4500 approved"
    assert red.redact(text) == text


def test_plausible_phone_is_caught(red):
    out = red.redact("call +44 7700 900123 tomorrow")
    assert "900123" not in out
    assert "<PHONE_1>" in out


def test_invalid_ip_octets_are_ignored(red):
    text = "version 999.888.777.666 released"
    assert red.redact(text) == text


# --- restoration ---------------------------------------------------------------


def test_values_round_trip(red):
    original = "Reply to sam@example.com on +44 7700 900123"
    redacted = red.redact(original)
    assert red.restore(redacted) == original


def test_restore_only_touches_known_placeholders(red):
    """A model inventing <EMAIL_9> must not be able to make one up and have it
    resolved to somebody's address."""
    red.redact("sam@example.com")
    assert red.restore("mail <EMAIL_9> now") == "mail <EMAIL_9> now"


def test_a_draft_written_against_placeholders_reaches_a_real_person(red):
    """The whole reason for substitution rather than deletion."""
    red.redact("From: sam@example.com")
    draft = "To: <EMAIL_1>\nSubject: dinner"
    assert red.restore(draft) == "To: sam@example.com\nSubject: dinner"


def test_empty_input_is_safe(red):
    assert red.redact("") == ""
    assert red.restore("") == ""
    assert red.redact(None) is None


# --- reporting -----------------------------------------------------------------


def test_summary_counts_without_revealing(red):
    red.redact("sam@example.com, alex@example.com, +44 7700 900123")
    summary = red.summary()
    assert summary["EMAIL"] == 2
    assert summary["PHONE"] == 1
    assert "sam@example.com" not in str(summary)


# --- honesty about limits ------------------------------------------------------


def test_names_are_not_claimed_to_be_caught(red):
    """A documented limitation, pinned so nobody later assumes otherwise.

    A pattern cannot find names or medical detail in prose, and claiming to
    remove personal data while leaving those in place would be worse than not
    claiming it.
    """
    text = "Sam's checkup result was clear, she lives at 14 Elm Street"
    assert red.redact(text) == text

    # Raw text on purpose: the subject here IS the documentation. The module
    # must state this limitation in words a reader will see, so stripping
    # docstrings would remove the very thing being checked. Marked
    # asserts-on-prose so tests/test_source_assertions.py allows it.
    doc = MODULE.read_text(encoding="utf-8")  # asserts-on-prose
    assert "does not reliably catch names" in doc
    assert "not as anonymisation" in doc


def pytest_redactor():
    spec = importlib.util.spec_from_file_location("pii2", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Redactor()


# --- the irreversible subset ---------------------------------------------------


def test_strip_removes_high_harm_identifiers(pii):
    out = pii.strip_sensitive("card 4111 1111 1111 1111, NI AB123456C, "
                              "key sk-abcdefghijklmnop")
    assert "4111" not in out and "AB123456C" not in out
    assert "sk-abcdefghijklmnop" not in out
    assert out.count("removed") == 3


def test_strip_keeps_what_the_assistant_needs(pii):
    """Emails and phones are absent from NEVER_NEEDED on purpose: the assistant
    cannot reply to anyone without them, so they need the reversible path."""
    text = "mail sam@example.com or call +44 7700 900123"
    assert pii.strip_sensitive(text) == text
    assert "EMAIL" not in pii.NEVER_NEEDED
    assert "PHONE" not in pii.NEVER_NEEDED


def test_strip_needs_no_state_to_reverse(pii):
    """The whole reason this half ships alone: nothing has to be remembered."""
    import inspect
    source = inspect.getsource(pii.strip_sensitive)
    assert "self" not in source and "cache" not in source


def test_strip_respects_checksums(pii):
    """An order number must survive stripping as much as it survives
    substitution."""
    text = "order 1234567812345678 shipped"
    assert pii.strip_sensitive(text) == text


# --- the format IBANs are actually written in ---------------------------------


@pytest.mark.parametrize("value", [
    "GB82 WEST 1234 5698 7654 32",      # UK, spaced, the common form
    "GB82WEST12345698765432",           # compact
    "DE89 3704 0044 0532 0130 00",      # German
    "FR14 2004 1010 0505 0001 3M02 606",  # French, with letters mid-string
])
def test_real_ibans_are_stripped_in_every_written_form(pii, value):
    """Banks, invoices and letters print IBANs in groups of four, so the spaced
    form must be caught as well as the compact one."""
    assert pii.strip_sensitive(value) == "[IBAN removed]"


@pytest.mark.parametrize("value", [
    "GB00 WEST 1234 5698 7654 32",        # checksum fails
    "Order AB12 3456 7890 was shipped",   # ordinary text
    "the meeting is at 10 00 today",
])
def test_the_looser_pattern_does_not_over_match(pii, value):
    """mod-97 is what makes it safe to allow spaces in the pattern."""
    assert pii.strip_sensitive(value) == value
