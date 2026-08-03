# Voice satellite (reference client)

A Raspberry Pi in a room that you can talk to. Records, transcribes locally,
sends **text** to Agentbox, speaks the reply locally.

```
  ┌─ Pi in the kitchen ──────────────────┐
  │  mic → whisper ──┐                   │
  │                  │  text over LAN    │        ┌─ agentbox ─────┐
  │                  ├──────────────────────────► │  the assistant │
  │  speaker ← piper ┘  ◄───────────────────────  │                │
  └──────────────────────────────────────┘        └────────────────┘
```

**Audio never leaves the satellite.** Only text crosses the network. That is
the point of putting the two small models on the Pi rather than streaming
microphone audio to the main box: a Pi cannot run a 30B-class model, but
Whisper and Piper are well within it — and a device that never transmits audio
cannot leak a live microphone.

## Status: the client is complete, the server endpoint is not

This client is written against a contract Agentbox does not serve yet:

```
POST {AGENTBOX_URL}/v1/say
Authorization: Bearer <token>
{"text": "what's on my calendar today", "satellite": "kitchen"}

200 {"reply": "You have two things..."}
```

`hermes webhook` accepts a POST and runs the agent, but delivers the reply to
Discord rather than returning it — so a satellite cannot speak the answer. The
missing piece is a small synchronous endpoint; see `docs/roadmap.md`.

`--dry-run` runs the entire audio loop against a canned reply, so you can set
up and validate a Pi today and point it at the real endpoint later.

## Setup

```bash
sudo apt-get install -y libportaudio2 ffmpeg
pip install faster-whisper piper-tts sounddevice numpy
python -m piper.download_voices en_US-lessac-medium --data-dir ~/piper-voices
```

Check the speaker, then the microphone:

```bash
./satellite.py --say "Kitchen satellite online."
```

```bash
./satellite.py --dry-run
```

Speak; it should print what it heard and read it back. If it never triggers,
lower `SATELLITE_SILENCE_RMS`; if it triggers on room noise, raise it.

Then point it at the box:

```bash
AGENTBOX_URL=http://agentbox.local:8770 \
AGENTBOX_SATELLITE_TOKEN=... \
./satellite.py --name kitchen
```

## Settings

| Variable | Default | |
|---|---|---|
| `AGENTBOX_URL` | `http://127.0.0.1:8770` | where the assistant lives |
| `AGENTBOX_SATELLITE_TOKEN` | — | bearer token; unauthenticated requests should be refused |
| `SATELLITE_NAME` | `satellite` | which room, sent with every request |
| `SATELLITE_WHISPER_MODEL` | `base.en` | `tiny.en` on a Pi 4 if `base.en` drags |
| `SATELLITE_PIPER_VOICE` | `~/piper-voices/en_US-lessac-medium.onnx` | |
| `SATELLITE_SILENCE_RMS` | `0.012` | speech threshold; tune per room |
| `SATELLITE_SILENCE_HANG` | `1.2` | seconds of quiet that end an utterance |

## Running it as a service

```ini
# /etc/systemd/system/agentbox-satellite.service
[Unit]
Description=Agentbox voice satellite
After=network-online.target sound.target

[Service]
User=pi
Environment=SATELLITE_NAME=kitchen
Environment=AGENTBOX_URL=http://agentbox.local:8770
EnvironmentFile=/etc/agentbox-satellite.env   # the token, mode 0600
ExecStart=/home/pi/satellite.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## What this deliberately does not do

- **No wake word.** It opens the microphone on any sound above a threshold,
  which is fine for a demo and wrong for an always-on device — you want
  openWakeWord or a ReSpeaker HAT with on-board detection so the Pi ignores
  everything until it hears its name.
- **No echo cancellation.** It can hear its own speaker. Use a HAT with AEC, or
  keep the mic and speaker apart.
- **No barge-in.** You cannot interrupt it mid-sentence.

Those three are exactly what Home Assistant's Assist stack and the ESPHome
voice firmware already solve. If you end up wanting more than one of these
around the house, that is the direction to go — this file is a reference for
how a client talks to Agentbox, not a product.

## Two things to settle before this becomes real

- **Which room heard it.** `satellite` is sent on every request and the reply
  has to come back to that device, or every answer plays everywhere at once.
- **What a voice request may do.** A spoken request has no operator reading it
  carefully first, so the Discord approval loop is a poor fit — a satellite
  probably wants a narrower capability set rather than the same one with
  approvals in front of it. Deciding that is a policy question, not a code one.
