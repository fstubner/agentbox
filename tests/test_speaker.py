"""Speech output: quiet hours, speaker naming, and what cannot be said.

Quiet hours are enforced in the process that owns the speaker, not as a policy
tier. A tier can be granted, and an approval only helps if a human reads it
carefully at 3am.
"""
from __future__ import annotations

import datetime
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest
from conftest import code_of

REPO = Path(__file__).resolve().parents[1]


def load(monkeypatch, quiet="22:00-07:00", speakers="kitchen:AA:BB:CC:DD:EE:FF"):
    monkeypatch.setenv("AGENTBOX_QUIET_HOURS", quiet)
    monkeypatch.setenv("AGENTBOX_SPEAKERS", speakers)
    monkeypatch.setenv("AGENTBOX_SPEAKER_TOKEN", "tok")
    loader = importlib.machinery.SourceFileLoader(
        "agentbox_speaker", str(REPO / "cli" / "agentbox-speaker"))
    spec = importlib.util.spec_from_loader("agentbox_speaker", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["agentbox_speaker"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def spk(monkeypatch):
    return load(monkeypatch)


def at(hour, minute=0):
    return datetime.time(hour, minute)


# --- quiet hours ---------------------------------------------------------------


def test_window_crossing_midnight_is_handled(spk):
    """A plain `start <= now <= end` check never matches a window that
    crosses midnight, such as 22:00-07:00."""
    assert spk.in_quiet_hours(at(23)) is True
    assert spk.in_quiet_hours(at(3)) is True
    assert spk.in_quiet_hours(at(22)) is True
    assert spk.in_quiet_hours(at(6, 59)) is True

    assert spk.in_quiet_hours(at(7)) is False
    assert spk.in_quiet_hours(at(12)) is False
    assert spk.in_quiet_hours(at(21, 59)) is False


def test_daytime_window_also_works(monkeypatch):
    """A window that does not cross midnight, for someone who works nights."""
    spk = load(monkeypatch, quiet="09:00-17:00")
    assert spk.in_quiet_hours(at(12)) is True
    assert spk.in_quiet_hours(at(8)) is False
    assert spk.in_quiet_hours(at(18)) is False


def test_unset_quiet_hours_never_refuses(monkeypatch):
    spk = load(monkeypatch, quiet="")
    assert spk.in_quiet_hours(at(3)) is False


def test_malformed_window_does_not_silently_block_everything(monkeypatch):
    """A typo in config should not make the assistant permanently mute with no
    explanation. Failing open is correct here, because the cost is speech at a
    bad hour, not a safety property."""
    spk = load(monkeypatch, quiet="10pm to 7am")
    assert spk.in_quiet_hours(at(3)) is False


def test_quiet_hours_refuse_rather_than_queue(spk, monkeypatch):
    """A message deferred until quiet hours end would play an old reminder in
    the morning."""
    monkeypatch.setattr(spk, "in_quiet_hours", lambda *a: True)
    spoken, detail = spk.say("the bins go out")
    assert spoken is False
    assert "Not queued" in detail
    assert "quiet hours" in detail


# --- speakers ------------------------------------------------------------------


def test_speakers_are_addressed_by_name_not_mac(spk):
    """The assistant asks for 'the kitchen'. It never learns a MAC, so it has
    no address it could point at something else."""
    assert spk.speakers() == {"kitchen": "AA:BB:CC:DD:EE:FF"}


def test_unknown_speaker_is_refused_and_lists_the_real_ones(spk, monkeypatch):
    monkeypatch.setattr(spk, "in_quiet_hours", lambda *a: False)
    spoken, detail = spk.say("hello", "bedroom")
    assert spoken is False
    assert "unknown speaker" in detail and "kitchen" in detail


def test_no_speakers_configured_says_so(monkeypatch):
    spk = load(monkeypatch, speakers="")
    monkeypatch.setattr(spk, "in_quiet_hours", lambda *a: False)
    assert spk.say("hello")[0] is False


# --- what cannot be said -------------------------------------------------------


def test_long_text_is_refused(spk, monkeypatch):
    monkeypatch.setattr(spk, "in_quiet_hours", lambda *a: False)
    spoken, detail = spk.say("x" * (spk.MAX_CHARS + 1))
    assert spoken is False
    assert "longer than a person will stand" in detail


def test_empty_text_is_refused(spk):
    assert spk.say("")[0] is False
    assert spk.say("   ")[0] is False


def test_there_is_no_route_that_plays_a_file_or_url():
    """Speech can carry an injected email to someone who is not looking at a
    screen. The tool takes composed text, never a document to read back."""
    source = code_of(REPO / "cli" / "agentbox-speaker")
    for forbidden in ("urlopen", "urlretrieve", '"file"', "def play_file"):
        assert forbidden not in source


def test_quiet_hours_are_enforced_where_the_speaker_lives(spk):
    """Not a policy tier. `agentbox grant speak_aloud` must not produce 3am
    speech, so the refusal is in this process and not in the gate."""
    import inspect
    assert "in_quiet_hours()" in inspect.getsource(spk.say)


def test_playback_failure_is_reported_not_swallowed(spk, monkeypatch):
    """Reporting success for speech nobody heard would make people distrust
    the feature."""
    monkeypatch.setattr(spk, "in_quiet_hours", lambda *a: False)
    monkeypatch.setattr(spk.os.path, "exists", lambda p: True)

    class Result:
        def __init__(self, code):
            self.returncode = code
            self.stderr = b"connection refused"

    def fake_run(cmd, *args, **kwargs):
        # Synthesis succeeds and playback fails, as when the speaker has moved
        # out of range since it last connected.
        return Result(0 if "piper" in cmd[0] else 1)

    monkeypatch.setattr(spk.subprocess, "run", fake_run)
    spoken, detail = spk.say("hello")
    assert spoken is False
    assert "playback failed" in detail
    assert "connection refused" in detail
