#!/usr/bin/env python3
"""Memory bridge on the shared bridge_base. Stores memories/proposals in a
JSON file behind a process-wide lock (single-writer semantics)."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from bridge_base import BridgeError, BridgeHandler, serve

MEMORY_PATH = Path(os.environ.get("MEMORY_PATH", "/data/memory.json"))
_LOCK = threading.Lock()


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_store() -> dict[str, Any]:
    if not MEMORY_PATH.exists():
        return {"memories": [], "proposals": []}
    return json.loads(MEMORY_PATH.read_text(encoding="utf-8"))


def save_store(store: dict[str, Any]) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MEMORY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MEMORY_PATH)


def clean_memory(body: dict[str, Any], status: str) -> dict[str, Any]:
    statement = str(body.get("statement", "")).strip()
    if not statement:
        raise BridgeError(400, "statement is required")
    return {
        "id": body.get("id") or str(uuid.uuid4()),
        "type": body.get("type", "profile_preference"),
        "statement": statement,
        "source": body.get("source", ""),
        "confidence": body.get("confidence", "medium"),
        "sensitivity": body.get("sensitivity", "medium"),
        "status": status,
        "created_at": body.get("created_at") or now(),
        "updated_at": now(),
        "metadata": body.get("metadata", {}),
    }


def filter_items(items: list[dict[str, Any]], query: dict[str, Any]) -> list[dict[str, Any]]:
    result = items
    for field in ("type", "status", "sensitivity"):
        wanted = query.get(field)
        if wanted:
            result = [x for x in result if x.get(field) == wanted]
    return result


# --- routes -----------------------------------------------------------------


def get_schema(handler, body):
    return 200, {"service": "memory-bridge", "tools": [
        "POST /v1/proposals",
        "GET /v1/proposals",
        "POST /v1/proposals/{id}/approve",
        "POST /v1/memories",
        "GET /v1/memories",
    ]}


def list_proposals(handler, body):
    with _LOCK:
        store = load_store()
    return 200, {"proposals": filter_items(store["proposals"], {})}


def create_proposal(handler, body):
    item = clean_memory(body or {}, "proposed")
    with _LOCK:
        store = load_store()
        store["proposals"].append(item)
        save_store(store)
    return 201, item


def create_memory(handler, body):
    item = clean_memory(body or {}, "approved")
    with _LOCK:
        store = load_store()
        store["memories"].append(item)
        save_store(store)
    return 201, item


def list_memories(handler, body):
    with _LOCK:
        store = load_store()
    return 200, {"memories": filter_items(store["memories"], {})}


def approve_proposal(proposal_id: str):
    with _LOCK:
        store = load_store()
        proposal = next((x for x in store["proposals"] if x.get("id") == proposal_id), None)
        if not proposal:
            raise BridgeError(404, "proposal not found")
        proposal["status"] = "approved"
        proposal["updated_at"] = now()
        store["memories"].append(proposal)
        store["proposals"] = [x for x in store["proposals"] if x.get("id") != proposal_id]
        save_store(store)
    return 200, proposal


class MemoryBridge(BridgeHandler):
    server_version = "memory-bridge/1.0"
    bridge_token = os.environ.get("MEMORY_BRIDGE_TOKEN", "")
    routes = {
        ("GET", "/schema"): get_schema,
        ("GET", "/v1/proposals"): list_proposals,
        ("POST", "/v1/proposals"): create_proposal,
        ("GET", "/v1/memories"): list_memories,
        ("POST", "/v1/memories"): create_memory,
    }

    def route_fallback(self, method: str, path: str, body):
        prefix, suffix = "/v1/proposals/", "/approve"
        if method == "POST" and path.startswith(prefix) and path.endswith(suffix):
            return approve_proposal(path[len(prefix):-len(suffix)])
        raise BridgeError(404, "not found")


if __name__ == "__main__":
    serve(MemoryBridge)
