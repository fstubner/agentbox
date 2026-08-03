# Voice

Speech in and out, entirely on this box. Nothing spoken to the assistant and
nothing it says back leaves the machine.

That is a deliberate choice rather than a default. The gateway shipped with
`tts.provider: edge`, which synthesises through Microsoft's cloud — so every
spoken reply, including anything the assistant had just read out of email or
the calendar to compose it, would have been sent off the box. Piper is
noticeably more synthetic to listen to and that is the trade accepted here.

| Stage | Component | Where it runs |
|---|---|---|
| Speech → text | faster-whisper (`base`) | local, CPU |
| Text → speech | Piper (`en_US-lessac-medium`) | local, CPU |
| Discord audio | libopus + ffmpeg | local |

Verified 2026-08-03 by round-tripping: text → Piper → WAV → faster-whisper →
text came back as the same sentence.

## What had to be installed

The gateway's config described a full voice stack; none of it was present, so
voice silently did nothing and the only symptom was one warning line at
startup.

```bash
sudo apt-get install -y libopus0 ffmpeg
```

```bash
sudo -u agentbox /home/agentbox/hermes-agent-test/.venv/bin/pip install faster-whisper piper-tts
```

Without `libopus0` the gateway logs `Opus codec not found — voice channel
playback disabled` on every start and Discord voice does not work at all. That
line was in the journal for weeks; it reads like a warning and is in practice a
feature being off.

Models are cached under the gateway's profile, not the operator's home:

- Piper voice — `/home/agentbox/agentbox/cache/piper-voices/`
- Whisper — `/home/agentbox/agentbox/cache/whisper/`

Pre-fetch the Piper voice so the first spoken reply is not a 60-second pause:

```bash
sudo -u agentbox env HERMES_HOME=/home/agentbox/agentbox /home/agentbox/hermes-agent-test/.venv/bin/python -m piper.download_voices en_US-lessac-medium --data-dir /home/agentbox/agentbox/cache/piper-voices
```

## Config

```yaml
stt:
  enabled: true
  provider: local          # faster-whisper on this box
  local:
    model: base
tts:
  provider: piper          # was `edge` (Microsoft cloud)
  piper:
    voice: en_US-lessac-medium
```

`voice.record_key` and the other `voice:` settings are for the interactive TUI
push-to-talk path, not Discord. They need `sounddevice`, which is not installed
and is not needed for any of the above.

## Checking it works

```bash
sudo journalctl -u hermes-gateway-agentbox --since "2 minutes ago" | grep -i opus
```

Silence is the pass condition. Any line mentioning Opus means the codec is
missing again and Discord voice is off.

## House-wide microphones and speakers

Not built. See `docs/roadmap.md` — the short version is that Home Assistant
already solves the satellite half (wake word, audio streaming, ESP32 and
Raspberry Pi firmware) and this platform should supply the thinking half
through a webhook, rather than reimplementing an audio pipeline.
