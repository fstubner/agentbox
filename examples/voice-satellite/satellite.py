#!/usr/bin/env python3
"""Reference voice satellite for Agentbox.

Runs on a Raspberry Pi (or any Linux box with a microphone and a speaker) and
gives you a spoken conversation with the assistant from another room.

    listen -> transcribe locally -> POST text -> speak the reply locally

**Audio never leaves this device.** Speech-to-text and text-to-speech both run
here; only text crosses the network. That is the whole reason to put Whisper
and Piper on the satellite rather than streaming microphone audio to the main
box: a Pi is far too slow to run the assistant's 30B-class model, but it is
perfectly capable of the two small models at either end of the conversation.
It also means a compromised network sees text, not a live microphone feed.

Sized for a Pi 4 or 5. On a Pi 4, `tiny.en` transcribes a short utterance in
about a second; `base.en` is noticeably better and roughly twice that. Piper
synthesises faster than real time on both.

## The one thing this cannot do yet

Agentbox has no synchronous conversation endpoint. `hermes webhook` accepts a
POST and runs the agent, but delivers the reply to a messaging platform such
as Discord rather than returning it to the caller. A satellite needs the reply
back in the HTTP response to speak it.

So this client is written against a small contract that does not exist on the
server yet:

    POST {AGENTBOX_URL}/v1/say
    Authorization: Bearer <token>
    {"text": "...", "satellite": "kitchen"}

    200 {"reply": "..."}

Everything on this side is complete and runnable. `--dry-run` exercises the
whole audio loop with a canned reply, so you can set up and test a Pi before
that endpoint exists. See `docs/roadmap.md` for the server half.

## Setup on the satellite

    sudo apt-get install -y libportaudio2 ffmpeg
    pip install faster-whisper piper-tts sounddevice numpy
    python -m piper.download_voices en_US-lessac-medium --data-dir ~/piper-voices

    ./satellite.py --dry-run          # check the microphone and speaker
    ./satellite.py --name kitchen     # once /v1/say exists

Set `AGENTBOX_URL` and `AGENTBOX_SATELLITE_TOKEN` in the environment.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

# Recording. 16 kHz mono is what Whisper wants and what cheap USB microphones
# and the ReSpeaker HATs produce natively, so nothing has to resample.
SAMPLE_RATE = 16_000
CHANNELS = 1
BLOCK_MS = 30

# Voice activity, by amplitude. Crude on purpose: proper VAD or a wake word is
# the right answer for an always-on device in a room (see the README), but this
# has no extra dependencies and is enough to prove the loop end to end.
SILENCE_RMS = float(os.environ.get("SATELLITE_SILENCE_RMS", "0.012"))
SILENCE_HANG_S = float(os.environ.get("SATELLITE_SILENCE_HANG", "1.2"))
MAX_UTTERANCE_S = float(os.environ.get("SATELLITE_MAX_UTTERANCE", "20"))
MIN_UTTERANCE_S = 0.4

AGENTBOX_URL = os.environ.get("AGENTBOX_URL", "http://127.0.0.1:8770").rstrip("/")
TOKEN = os.environ.get("AGENTBOX_SATELLITE_TOKEN", "")
REQUEST_TIMEOUT = float(os.environ.get("SATELLITE_TIMEOUT", "120"))

WHISPER_MODEL = os.environ.get("SATELLITE_WHISPER_MODEL", "base.en")
PIPER_VOICE = os.environ.get(
    "SATELLITE_PIPER_VOICE",
    str(Path("~/piper-voices/en_US-lessac-medium.onnx").expanduser()))


def log(message: str) -> None:
    print(f"[satellite] {message}", flush=True)


# --- audio ------------------------------------------------------------------


def record_utterance() -> bytes | None:
    """Block until someone speaks, then return PCM once they stop.

    Returns None if the utterance was too short to be speech. A door closing
    and a cough both trip an amplitude gate, and transcribing them wastes a
    couple of seconds and occasionally invents a sentence.
    """
    import numpy as np
    import sounddevice as sd

    blocks: queue.Queue = queue.Queue()

    def on_audio(indata, _frames, _time, status):
        if status:
            log(f"audio status: {status}")
        blocks.put(bytes(indata))

    block_frames = int(SAMPLE_RATE * BLOCK_MS / 1000)
    captured: list[bytes] = []
    speaking = False
    silence_started = 0.0
    started = time.monotonic()

    with sd.RawInputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                           dtype="int16", blocksize=block_frames,
                           callback=on_audio):
        while True:
            try:
                block = blocks.get(timeout=1.0)
            except queue.Empty:
                continue

            samples = np.frombuffer(block, dtype=np.int16).astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0

            if rms >= SILENCE_RMS:
                if not speaking:
                    log("listening…")
                    speaking = True
                silence_started = 0.0
                captured.append(block)
            elif speaking:
                captured.append(block)
                now = time.monotonic()
                if not silence_started:
                    silence_started = now
                elif now - silence_started >= SILENCE_HANG_S:
                    break

            if speaking and time.monotonic() - started > MAX_UTTERANCE_S:
                log("hit the utterance cap")
                break

    audio = b"".join(captured)
    seconds = len(audio) / (2 * SAMPLE_RATE)
    if seconds < MIN_UTTERANCE_S:
        return None
    return audio


def write_wav(pcm: bytes, path: str) -> None:
    with wave.open(path, "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm)


def play_wav(path: str) -> None:
    """Play through aplay rather than a Python audio library.

    alsa-utils is on every Raspberry Pi image, so this drops the `soundfile`
    dependency, one fewer wheel to build on a Pi, where native builds
    are slow and occasionally fail outright. Set SATELLITE_PLAY_CMD to override
    (`paplay` for PulseAudio, or a command that targets a specific device).
    """
    import shlex
    import subprocess

    command = os.environ.get("SATELLITE_PLAY_CMD", "aplay -q")
    try:
        subprocess.run([*shlex.split(command), path], check=True,
                       stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        log(f"playback command not found: {command.split()[0]} "
            "(install alsa-utils, or set SATELLITE_PLAY_CMD)")
    except subprocess.CalledProcessError as exc:
        log(f"playback failed ({exc.returncode}); is a sound card configured?")


# --- models -----------------------------------------------------------------


class Ears:
    """Whisper, loaded once. Model load is slow; transcription is not."""

    def __init__(self, model_name: str) -> None:
        from faster_whisper import WhisperModel
        log(f"loading whisper '{model_name}'…")
        # int8 on CPU: on a Pi this is the difference between usable and not.
        self.model = WhisperModel(model_name, device="cpu", compute_type="int8")

    def transcribe(self, wav_path: str) -> str:
        segments, _ = self.model.transcribe(wav_path, beam_size=1,
                                            vad_filter=True)
        return " ".join(segment.text.strip() for segment in segments).strip()


class Mouth:
    """Piper, loaded once."""

    def __init__(self, voice_path: str) -> None:
        from piper import PiperVoice
        if not Path(voice_path).exists():
            sys.exit(f"piper voice not found: {voice_path}\n"
                     f"  python -m piper.download_voices en_US-lessac-medium "
                     f"--data-dir {Path(voice_path).parent}")
        log(f"loading piper voice {Path(voice_path).name}…")
        self.voice = PiperVoice.load(voice_path)

    def say(self, text: str) -> None:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = handle.name
        try:
            with wave.open(path, "wb") as out:
                self.voice.synthesize_wav(text, out)
            play_wav(path)
        finally:
            Path(path).unlink(missing_ok=True)


# --- transport --------------------------------------------------------------


def ask(text: str, name: str) -> str:
    """Send the transcript and return what to say back."""
    body = json.dumps({"text": text, "satellite": name}).encode()
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = urllib.request.Request(f"{AGENTBOX_URL}/v1/say", data=body,
                                     headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return (json.loads(response.read()) or {}).get("reply", "")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        log(f"agentbox returned {exc.code}: {detail}")
        # Spoken, not just logged. A satellite has no screen, and silence would
        # look the same as "it did not hear me".
        return "Sorry, I could not reach the assistant."
    except urllib.error.URLError as exc:
        log(f"agentbox unreachable: {exc.reason}")
        return "Sorry, I could not reach the assistant."


# --- loop -------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Agentbox voice satellite")
    parser.add_argument("--name", default=os.environ.get("SATELLITE_NAME", "satellite"),
                        help="which room this is; sent with every request")
    parser.add_argument("--dry-run", action="store_true",
                        help="exercise the audio loop with a canned reply, "
                             "without contacting agentbox")
    parser.add_argument("--say", metavar="TEXT",
                        help="speak TEXT and exit (speaker check)")
    parser.add_argument("--once", action="store_true",
                        help="handle a single utterance and exit")
    args = parser.parse_args()

    mouth = Mouth(PIPER_VOICE)
    if args.say:
        mouth.say(args.say)
        return 0

    ears = Ears(WHISPER_MODEL)
    log(f"ready as '{args.name}'"
        + (" (dry run)" if args.dry_run else f" -> {AGENTBOX_URL}"))
    if not args.dry_run and not TOKEN:
        log("warning: AGENTBOX_SATELLITE_TOKEN is unset; requests will be "
            "unauthenticated and should be refused")

    while True:
        pcm = record_utterance()
        if pcm is None:
            continue

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            wav_path = handle.name
        try:
            write_wav(pcm, wav_path)
            heard = ears.transcribe(wav_path)
        finally:
            Path(wav_path).unlink(missing_ok=True)

        if not heard:
            continue
        log(f"heard: {heard}")

        if args.dry_run:
            reply = f"You said: {heard}"
        else:
            reply = ask(heard, args.name)

        if reply:
            log(f"saying: {reply[:100]}")
            mouth.say(reply)

        if args.once:
            return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(0)
