# Voice satellite (reference client)

A Raspberry Pi in a room that you can talk to. It records, transcribes
locally, sends text to Agentbox, and speaks the reply locally.

```
  ┌─ Pi in the kitchen ──────────────────┐
  │  mic → whisper ──┐                   │
  │                  │  text over LAN    │        ┌─ agentbox ─────┐
  │                  ├──────────────────────────► │  the assistant │
  │  speaker ← piper ┘  ◄───────────────────────  │                │
  └──────────────────────────────────────┘        └────────────────┘
```

Audio never leaves the satellite, and only text crosses the network. A Pi
cannot run the main model, but it can run Whisper and Piper, and a device that
never sends audio cannot leak a live microphone.

This is a different route from the one in `docs/voice.md`, which goes through
Home Assistant's Assist pipeline. This client talks to Agentbox directly.

## Status

The client is finished. The server endpoint it needs does not exist yet.

```
POST {AGENTBOX_URL}/v1/say
Authorization: Bearer <token>
{"text": "what's on my calendar today", "satellite": "kitchen"}

200 {"reply": "You have two things..."}
```

The gateway's webhook runs the agent but delivers the reply to Discord rather
than returning it, so a satellite could not speak the answer. What is missing
is a small endpoint that answers directly (`docs/roadmap.md`).

`--dry-run` runs the whole audio loop against a canned reply, so a Pi can be
set up and checked now and pointed at the real endpoint later.

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

Speak, and it should print what it heard and read it back. If it never
triggers, lower `SATELLITE_SILENCE_RMS`. If room noise triggers it, raise it.

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
| `AGENTBOX_SATELLITE_TOKEN` | | bearer token, and requests without it should be refused |
| `SATELLITE_NAME` | `satellite` | which room, sent with every request |
| `SATELLITE_WHISPER_MODEL` | `base.en` | `tiny.en` on a Pi 4 if `base.en` drags |
| `SATELLITE_PIPER_VOICE` | `~/piper-voices/en_US-lessac-medium.onnx` | |
| `SATELLITE_SILENCE_RMS` | `0.012` | speech threshold, tuned per room |
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

## What it does not do

- **No wake word.** It listens on any sound above a threshold, which is fine
  for a demo and wrong for an always-on device. openWakeWord, or a ReSpeaker
  HAT with on-board detection, would make it wait for its name.
- **No echo cancellation.** It can hear its own speaker. Use a HAT with echo
  cancellation, or keep the microphone and speaker apart.
- **No interrupting.** You cannot stop it mid-sentence.

Home Assistant's Assist stack and ESPHome voice firmware already solve all
three, so for more than one device around the house, use those. This is a
reference for how a client talks to Agentbox, not a product.

## Two questions to settle first

- **Which room heard it.** `satellite` is sent with every request, and the
  reply has to come back to that device, or every answer plays everywhere.
- **What a spoken request may do.** Nobody reads a spoken request carefully
  before it runs, so the Discord approval loop fits badly. A satellite should
  get a narrower set of tools rather than the same set behind approvals.
