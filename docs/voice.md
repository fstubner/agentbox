# Voice

> Paths below use the deployment variables rather than one machine's literal
> layout: `$HERMES_HOME` is the gateway profile, `$GATEWAY_USER_HOME` the
> gateway user's home, `$GATEWAY_VENV` its virtualenv, `$AGENTBOX_ENV_DIR` the
> operator's env files, `$AGENTBOX_REPO` this checkout. CI refuses literal home
> directories so the repo stays portable and free of one person's filesystem.


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
sudo -u agentbox $GATEWAY_VENV/bin/pip install faster-whisper piper-tts
```

Without `libopus0` the gateway logs `Opus codec not found — voice channel
playback disabled` on every start and Discord voice does not work at all. That
line was in the journal for weeks; it reads like a warning and is in practice a
feature being off.

Models are cached under the gateway's profile, not the operator's home:

- Piper voice — `$HERMES_HOME/cache/piper-voices/`
- Whisper — `$HERMES_HOME/cache/whisper/`

Pre-fetch the Piper voice so the first spoken reply is not a 60-second pause:

```bash
sudo -u agentbox env HERMES_HOME=$HERMES_HOME $GATEWAY_VENV/bin/python -m piper.download_voices en_US-lessac-medium --data-dir $HERMES_HOME/cache/piper-voices
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

Home Assistant owns the audio; this box owns the thinking. The satellite half —
wake word, streaming, echo cancellation, firmware for cheap boards — is what
Home Assistant's Assist stack already does well, and reimplementing it here
would be months of work to arrive somewhere worse.

Four pieces. Two of them are here, one is hardware, one is not built.

### 1. Local transcription and speech — `services/compose/wyoming-{whisper,piper}`

Home Assistant on this deployment is a plain container, not Home Assistant OS,
so there is no add-on store and the Assist pipeline ships with
`stt_engine: None` and `tts_engine: None`. A satellite without them wakes,
streams, and is met with silence.

These two serve the same models the gateway already uses — Whisper `base` and
`en_US-lessac-medium` — over the Wyoming protocol, bound to `127.0.0.1`.
Home Assistant runs with host networking, so it reaches them there without
either port touching the LAN.

```bash
cli/agentbox deploy wyoming-whisper
cli/agentbox deploy wyoming-piper
```

They cost memory continuously, on a box whose defining constraint is 16 GB
shared with a model server. Limits are set (1536m and 512m) rather than left
open, because an unbounded transcriber is how that becomes a swap storm in the
middle of somebody's sentence.

Then in Home Assistant: **Settings → Devices → Add integration → Wyoming
Protocol**, twice — `127.0.0.1:10300` for speech-to-text and `127.0.0.1:10200`
for text-to-speech. Then **Settings → Voice assistants** and set both on the
pipeline.

### 2. A satellite — the Raspberry Pi

Any Pi with a microphone works. Install `wyoming-satellite` on it, point it at
this box's Home Assistant, and it appears as a device to assign a pipeline to.
Its whole job is audio: it never talks to Agentbox directly.

Prefer this over the alternative below. A Pi keeps the promise at the top of
this file; the alternative does not.

### 3. The Echos — supported, and they break locality

This house has five Alexa devices, a Chromecast and a Sony TV, and Home
Assistant can speak a reply through any of them. That works today with no
purchase and no wiring.

State the cost plainly, because it is the exact thing this document exists to
refuse: audio played through an Echo goes through Amazon. Piper synthesises the
words locally and then hands them to a device that is cloud-coupled by design,
so the reply leaves the box. For a shopping-list confirmation that may be a
trade worth making; for anything read out of email it is not.

Use them for output where convenience wins, and understand that any room whose
only speaker is an Echo is a room where `PRODUCT.md`'s "nothing spoken leaves
the box" is not true.

### 4. The route back to the assistant — not built

Home Assistant's own conversation agent answers today. Pointing a pipeline at
*this* assistant means a webhook: `hermes webhook subscribe` already provides
the entry point, with HMAC secrets and per-target delivery, so the Agentbox side
is a route and a policy decision rather than a new service.

Two things settle before it is built, both from `docs/roadmap.md` and both still
open:

- **Which room heard it.** The satellite id has to survive the round trip or
  every reply comes back everywhere at once.
- **What a spoken request is allowed to do.** A spoken request has no operator
  reading carefully before it lands, so an approval is a worse fit here than in
  Discord. Voice wants a *narrower* capability set rather than the same one with
  a prompt in front of it — which under this platform's own rule, that absence
  beats gating, means the voice route simply does not carry those tools.

  `policies/approval-policy.yaml` cannot express that today: it has tiers and a
  tool map, and no per-channel dimension. Adding a policy section that nothing
  enforces would be worse than adding none, so the decision belongs with the
  route, not ahead of it.
