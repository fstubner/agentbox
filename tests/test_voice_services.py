"""The two services that let voice leave Discord without leaving the box.

`docs/voice.md` trades speech quality for locality on purpose: nothing spoken
to the assistant and nothing it says back leaves the machine. The gateway
shipped configured for a cloud synthesiser and that was changed for exactly
this reason.

Extending voice to the rest of the house is where that promise is easiest to
lose by accident — every speaker already here is a cloud-coupled one. These
tests hold the part under this repository's control: the transcriber and the
synthesiser stay on this box, and neither port reaches the network.

## Why the comments are stripped rather than the YAML parsed

These compose files argue for loopback binding at length in their own
comments, so grepping the raw text would match the reasoning whether or not
the `ports:` line agreed with it — the suite's own recurring bug, which
`conftest.strip_comments` exists to prevent.

An earlier version parsed the YAML with PyYAML instead. That was a better
assertion and a worse test: PyYAML is not in the standard library and was not
declared anywhere, so it passed locally on a machine that happened to have it
and failed collection in CI. Everything else that runs on this host is stdlib
only; a test is not the place to break that.
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

    Home Assistant runs with host networking and reaches these on loopback;
    nothing else has any business doing so.
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
    """This box is 16 GB shared with a model server. An unbounded transcriber
    is how that becomes a swap storm mid-sentence."""
    name, cfg = service
    assert "mem_limit:" in cfg, name
    assert "no-new-privileges:true" in cfg, name
    assert "cap_drop:" in cfg and "ALL" in cfg, name


def test_they_speak_and_listen_as_the_gateway_already_does(service):
    """A reply should not change character depending on whether it arrived
    through Discord or through a room."""
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
    """They are supported and they are cloud-coupled. Saying only the first
    would make this file's own headline claim false in any room whose only
    speaker is one of them."""
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
