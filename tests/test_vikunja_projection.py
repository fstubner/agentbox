"""Tests for vikunja-bridge projection pushdown (docs/context-economy.md §1).

These guard the two properties the context-economy v1 measurement depends on:
the lean view must never invent or alter data, and `full` must stay the default
so enabling the parameter cannot silently change live gateway behaviour.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BASE_APP = REPO / "services" / "templates" / "bridge" / "app"
BRIDGE = REPO / "services" / "compose" / "vikunja-bridge" / "app" / "bridge.py"

sys.path.insert(0, str(BASE_APP))
spec = importlib.util.spec_from_file_location("vikunja_bridge", BRIDGE)
vb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vb)


FULL_TASK = {
    "id": 7, "title": "file taxes", "done": False, "priority": 3,
    "description": "<p>long html body</p>", "created": "2026-07-01T00:00:00Z",
    "hex_color": "", "percent_done": 0, "identifier": "#7",
}


def test_default_view_is_full():
    assert vb.require_view({}) == "full"


def test_lean_view_accepted():
    assert vb.require_view({"view": ["lean"]}) == "lean"


def test_unknown_view_rejected():
    with pytest.raises(vb.BridgeError) as exc:
        vb.require_view({"view": ["tiny"]})
    assert exc.value.status == 400


def test_projection_keeps_only_lean_fields():
    assert vb.project_tasks([FULL_TASK]) == [
        {"id": 7, "title": "file taxes", "done": False, "priority": 3}
    ]


def test_projection_preserves_values_exactly():
    """Lossless-by-omission: retained values are identical to the source."""
    projected = vb.project_tasks([FULL_TASK])[0]
    for key, value in projected.items():
        assert value == FULL_TASK[key]


def test_projection_omits_absent_keys_rather_than_nulling():
    """A missing field must not become an invented null."""
    assert vb.project_tasks([{"id": 1, "title": "x"}]) == [{"id": 1, "title": "x"}]


def test_projection_passes_through_non_list_payloads():
    """Vikunja error/empty bodies must not be reshaped into a list."""
    assert vb.project_tasks(None) is None
    assert vb.project_tasks({"vikunja_error": "boom"}) == {"vikunja_error": "boom"}


def test_projection_skips_non_dict_entries():
    assert vb.project_tasks([FULL_TASK, "junk", None]) == [
        {"id": 7, "title": "file taxes", "done": False, "priority": 3}
    ]


def test_schema_advertises_the_view_parameter():
    """The agent can only opt into lean if the schema tells it the param exists."""
    _, schema = vb.get_schema(None, None)
    view = schema["views"]["GET /v1/tasks"]
    assert view["param"] == "view"
    assert view["default"] == "full"
    assert "lean" in view["values"]
