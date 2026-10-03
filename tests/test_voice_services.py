"""The two services that let voice work outside Discord and stay on the box.

`docs/voice.md` trades speech quality for keeping speech on this machine.
Voice around the house is where that is easiest to lose, since many speakers
depend on the cloud. These tests cover the part this repository controls. The
transcriber and synthesiser stay on this box, and neither port reaches the
network.

## Why comments are stripped instead of parsing the YAML

The compose files explain loopback binding in their own comments, so a raw
text search would match the explanation whether or not the `ports:` line
agreed. Parsing the YAML would need PyYAML, which is not in the standard
library.
"""
from __future__ import annotations

import pytest
from conftest import REPO_ROOT as REPO
from conftest import strip_comments

SERVICES = ("wyoming-whisper", "wyoming-piper")


@pytest.fixture(params=SERVICES)
def service(request):
    path = REPO / "services" / "compose" / request.param / "compose.yaml"
    return request.param, strip_comments(path.read_text(encoding="utf-8"))


def test_neither_port_is_reachable_from_the_network(service):
    """A transcriber on the LAN is a microphone feed on the LAN.

    Home Assistant runs with host networking and reaches these on loopback.
    Nothing else needs to reach them.
    """
    name, cfg = service
    published = [line.strip().lstrip("- ").strip('"')
                 for line in cfg.splitlines() if ":10300:" in line or ":10200:" in line]
    assert published, f"{name}: no published port found"
    for entry in published:
        assert entry.startswith("127.0.0.1:"), f"{name}: {entry}"
    for wide in ("0.0.0.0:", "${LAN_BIND_IP"):
        assert wide not in cfg, f"{name} exposes {wide}"


def test_both_declare_a_memory_ceiling(service):
    """This box has 16 GB shared with a model server. An unbounded transcriber
    can push it into heavy swapping mid-sentence."""
    name, cfg = service
    assert "mem_limit:" in cfg, name
    assert "no-new-privileges:true" in cfg, name
    assert "cap_drop:" in cfg and "ALL" in cfg, name


def test_they_speak_and_listen_as_the_gateway_already_does(service):
    """A reply should sound the same whether it arrives through Discord or
    through a room."""
    name, cfg = service
    if name == "wyoming-piper":
        assert "en_US-lessac-medium" in cfg
    else:
        assert "--model" in cfg and "base" in cfg


def test_neither_runs_privileged(service):
    name, cfg = service
    assert "privileged: true" not in cfg, name
    assert "network_mode: host" not in cfg, name


# --- what the documentation has to keep saying --------------------------------


def test_the_voice_doc_does_not_pretend_cloud_speakers_are_local():
    """Cloud speakers are supported and depend on the cloud. Stating only the
    first would make the doc's main claim false in any room whose only
    speaker is one of them."""
    doc = (REPO / "docs" / "voice.md").read_text(encoding="utf-8")  # asserts-on-prose
    assert "goes through Amazon" in doc
    assert "not true" in doc


def test_the_unbuilt_half_is_named_as_unbuilt():
    """Home Assistant's own agent answers for now. A reader who deployed these
    two services expecting to talk to the assistant would be talking to Home
    Assistant."""
    doc = (REPO / "docs" / "voice.md").read_text(encoding="utf-8")  # asserts-on-prose
    assert "not built" in doc.lower()
    assert "webhook" in doc
