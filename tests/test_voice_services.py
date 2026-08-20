"""The two services that let voice leave Discord without leaving the box.

`docs/voice.md` trades speech quality for locality on purpose: nothing spoken
to the assistant and nothing it says back leaves the machine. The gateway
shipped configured for a cloud synthesiser and that was changed for exactly
this reason.

Extending voice to the rest of the house is where that promise is easiest to
lose by accident — every speaker already here is an Echo, a Chromecast or a
Sony TV. These tests hold the part under this repository's control: the
transcriber and the synthesiser stay on this box, and neither port reaches the
network.

Asserted against parsed YAML rather than the file's text. These compose files
argue for loopback binding at length in their comments, so grepping for
`127.0.0.1` would match the reasoning whether or not the `ports:` line agreed
with it — the suite's own recurring bug.
"""
from __future__ import annotations

import pytest
import yaml
from conftest import REPO_ROOT as REPO

SERVICES = ("wyoming-whisper", "wyoming-piper")


@pytest.fixture(params=SERVICES)
def service(request):
    path = REPO / "services" / "compose" / request.param / "compose.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    return request.param, config["services"][request.param]


def test_neither_port_is_reachable_from_the_network(service):
    """A transcriber on the LAN is a microphone feed on the LAN.

    Home Assistant runs with host networking and reaches these on loopback;
    nothing else has any business doing so.
    """
    name, spec = service
    published = spec.get("ports", [])
    assert published, name
    for entry in published:
        assert str(entry).startswith("127.0.0.1:"), f"{name}: {entry}"


def test_both_declare_a_memory_ceiling(service):
    """This box is 16 GB shared with a model server. An unbounded transcriber
    is how that becomes a swap storm mid-sentence."""
    name, spec = service
    assert spec.get("mem_limit"), name
    assert "no-new-privileges:true" in spec.get("security_opt", []), name
    assert spec.get("cap_drop") == ["ALL"], name


def test_they_speak_and_listen_as_the_gateway_already_does(service):
    """A reply should not change character depending on whether it arrived
    through Discord or through a room."""
    name, spec = service
    command = [str(part) for part in spec.get("command", [])]
    if name == "wyoming-piper":
        assert "en_US-lessac-medium" in command
    else:
        assert "--model" in command and "base" in command


def test_neither_runs_privileged(service):
    name, spec = service
    assert not spec.get("privileged")
    assert "host" != spec.get("network_mode")


# --- what the documentation has to keep saying --------------------------------


def test_the_voice_doc_does_not_pretend_echos_are_local():
    """They are supported and they are cloud-coupled. Saying only the first
    would make this file's own headline claim false in any room whose only
    speaker is an Echo."""
    doc = (REPO / "docs" / "voice.md").read_text(encoding="utf-8")  # asserts-on-prose
    assert "goes through Amazon" in doc
    assert "not true" in doc


def test_the_unbuilt_half_is_named_as_unbuilt():
    """Home Assistant's own agent answers today. A reader who deployed these
    two services and expected to be talking to the assistant would in fact be
    talking to Home Assistant."""
    doc = (REPO / "docs" / "voice.md").read_text(encoding="utf-8")  # asserts-on-prose
    assert "not built" in doc.lower()
    assert "webhook" in doc
