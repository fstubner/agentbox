# Voice

Speech recognition and synthesis run on this box. Nothing spoken to the
assistant and nothing it says back leaves the machine.

This is not the gateway default. The gateway ships with `tts.provider: edge`,
which synthesises speech through Microsoft's cloud. Every spoken reply would
leave the box, including anything the assistant had just read out of email or
a calendar to compose it. Piper sounds more synthetic than the cloud voice.

| Stage | Component | Where it runs |
|---|---|---|
| Speech to text | faster-whisper (`base`) | local, CPU |
| Text to speech | Piper (`en_US-lessac-medium`) | local, CPU |
| Discord audio | libopus and ffmpeg | local |

I checked it with a round trip. Text went through Piper to a WAV file, then
through faster-whisper, and came back as the same sentence.

Paths below use deployment variables. `$HERMES_HOME` is the gateway profile,
`$GATEWAY_VENV` its virtualenv, and `$AGENTBOX_ENV_DIR` the operator's env
files.

## Install

```bash
sudo apt-get install -y libopus0 ffmpeg
```

```bash
sudo -u agentbox $GATEWAY_VENV/bin/pip install faster-whisper piper-tts
```

Without `libopus0` the gateway logs `Opus codec not found` on every start and
Discord voice does not work. The message looks like a warning, but it means
the feature is off.

Models are cached under the gateway's profile, not the operator's home.

- Piper voices in `$HERMES_HOME/cache/piper-voices/`
- Whisper in `$HERMES_HOME/cache/whisper/`

Fetch the Piper voice in advance, or the first spoken reply waits about a
minute while it downloads.

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
  provider: piper          # not `edge`, which is Microsoft's cloud
  piper:
    voice: en_US-lessac-medium
```

`voice.record_key` and the other `voice:` settings are for push-to-talk in the
terminal UI, not Discord. They need `sounddevice`, which nothing above
requires.

## Check it works

```bash
sudo journalctl -u hermes-gateway-agentbox --since "2 minutes ago" | grep -i opus
```

No output is a pass. Any line mentioning Opus means the codec is missing and
Discord voice is off.

## Microphones and speakers around the house

Home Assistant handles the audio and this box runs the assistant. Wake words,
streaming, echo cancellation and firmware for cheap boards are handled by Home
Assistant's Assist stack, so this repository does not reimplement them.

There are four pieces. Two are in this repository, one is hardware, and one is
not built yet.

### 1. Local transcription and speech

`services/compose/wyoming-whisper` and `services/compose/wyoming-piper`.

Home Assistant here runs as a plain container, not Home Assistant OS, so there
is no add-on store. Its Assist pipeline starts with no speech-to-text or
text-to-speech engine. A satellite without them wakes up, streams audio and
gets no reply.

These two services serve the same models the gateway uses, over the Wyoming
protocol, bound to `127.0.0.1`. Home Assistant uses host networking, so it
reaches them there and neither port is exposed to the LAN.

```bash
cli/agentbox deploy wyoming-whisper
cli/agentbox deploy wyoming-piper
```

Both use memory all the time, on a box with 16 GB shared with the model
server. They have limits (1536m and 512m) so a transcriber cannot push the
machine into swap while it handles a request.

Then in Home Assistant, go to **Settings > Devices > Add integration > Wyoming
Protocol** and add it twice, with `127.0.0.1:10300` for speech to text and
`127.0.0.1:10200` for text to speech. Then select both on the pipeline under
**Settings > Voice assistants**.

### 2. A satellite

Any Raspberry Pi with a microphone works. Install `wyoming-satellite` on it and
point it at Home Assistant, and it shows up as a device you can assign a
pipeline to. It only handles audio and never talks to Agentbox directly.

Prefer this to the option below. With a Pi, no audio leaves the box.

### 3. Echo and other smart speakers

Home Assistant can speak a reply through an Alexa device, a Chromecast or a
smart TV. That works with nothing to buy or wire up.

The cost is that the reply leaves the box. Audio played through an Echo
goes through Amazon. Piper makes the speech locally and then hands it to a
device that depends on the cloud. That may be acceptable for a shopping-list
confirmation. It is not acceptable for anything read out of email.

Use them for output where convenience matters more than privacy. In a room
whose only speaker is an Echo, "nothing spoken leaves the box" is not true.

### 4. The route back to the assistant

This is not built. Home Assistant's own conversation agent answers today.

Pointing a pipeline at this assistant needs a webhook. `hermes webhook
subscribe` already provides the entry point, with HMAC secrets and per-target
delivery. The Agentbox side needs a route and a policy decision, not a new
service.

Two questions need answers first.

- **Which room heard it.** The satellite id has to survive the round trip, or
  every reply plays everywhere at once.
- **What a spoken request may do.** Nobody reads a spoken request carefully
  before it runs, so an approval prompt works worse for voice than for
  Discord. Voice should get a smaller set of tools. The other tools would have
  no route from voice, so a spoken request cannot call them, even with an
  approval.

  `policies/approval-policy.yaml` cannot express that yet. It has tiers and a
  tool map but no per-channel dimension. A policy section that nothing enforces
  would be misleading, so that decision will be made when the route is built.
