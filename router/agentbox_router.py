#!/usr/bin/env python3
"""Small local router for agentbox model roles.

This intentionally does not make model decisions with an LLM. It exposes
stable role endpoints so Hermes or another controller can ask for the right
kind of help without knowing which backend is currently assigned.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


MAIN_BASE = os.environ.get("AGENTBOX_MAIN_BASE", "http://127.0.0.1:1234/v1")
CONTEXT_BASE = os.environ.get("AGENTBOX_CONTEXT_BASE", "http://127.0.0.1:1235/v1")
REASON_BASE = os.environ.get("AGENTBOX_REASON_BASE", "http://127.0.0.1:1236/v1")

MAIN_MODEL = os.environ.get("AGENTBOX_MAIN_MODEL", "qwen36-35b-a3b-q4")
CONTEXT_MODEL = os.environ.get("AGENTBOX_CONTEXT_MODEL", "fastcontext-worker")
REASON_MODEL = os.environ.get("AGENTBOX_REASON_MODEL", "vibethinker-worker")

KEY_FILE = os.environ.get("AGENTBOX_LLAMA_KEY_FILE", os.path.expanduser("~/.config/llama-server/api_key.txt"))
TIMEOUT_SECONDS = float(os.environ.get("AGENTBOX_ROUTER_TIMEOUT_SECONDS", "600"))


def read_key() -> str:
    try:
        with open(KEY_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def post_json(url: str, body: dict[str, Any], timeout: float = TIMEOUT_SECONDS) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    key = read_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json(url: str, timeout: float = 10) -> dict[str, Any]:
    headers = {}
    key = read_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def final_text(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    return strip_thinking(str(msg.get("content") or ""))


def strip_thinking(text: str) -> str:
    """Return the usable final answer from thinking-style model output."""
    if "</think>" in text:
        return text.split("</think>", 1)[1].strip()
    if text.lstrip().startswith("<think>"):
        # Some small reasoning models omit the closing tag. In that case leave
        # the last paragraph only, which is usually the concise conclusion.
        parts = [p.strip() for p in text.split("\n\n") if p.strip()]
        return parts[-1] if parts else text
    return text


def chat(base: str, model: str, messages: list[dict[str, str]], **params: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": params.pop("temperature", 0),
        "max_tokens": params.pop("max_tokens", 1024),
    }
    body.update({k: v for k, v in params.items() if v is not None})
    started = time.time()
    raw = post_json(f"{base}/chat/completions", body)
    return {
        "model": model,
        "base": base,
        "elapsed_seconds": round(time.time() - started, 3),
        "text": final_text(raw),
        "raw": raw,
    }


def user_text(payload: dict[str, Any]) -> str:
    text = payload.get("text") or payload.get("prompt") or payload.get("input")
    if text is None:
        raise ValueError("expected one of: text, prompt, input")
    return str(text)


def context_extract(payload: dict[str, Any]) -> dict[str, Any]:
    text = user_text(payload)
    instruction = payload.get(
        "instruction",
        "Extract the facts, decisions, open questions, entities, dates, and action items. Keep it compact.",
    )
    messages = [
        {"role": "system", "content": "You are a context extraction worker. Return concise structured notes only."},
        {"role": "user", "content": f"{instruction}\n\n--- INPUT ---\n{text}"},
    ]
    return chat(CONTEXT_BASE, CONTEXT_MODEL, messages, max_tokens=int(payload.get("max_tokens", 1200)))


def reason_check(payload: dict[str, Any]) -> dict[str, Any]:
    text = user_text(payload)
    instruction = payload.get(
        "instruction",
        "Check the reasoning. Identify contradictions, missing assumptions, and the most likely correct conclusion.",
    )
    messages = [
        {"role": "system", "content": "You are a reasoning verifier. Think carefully, then give a concise final answer."},
        {"role": "user", "content": f"{instruction}\n\n--- INPUT ---\n{text}"},
    ]
    return chat(
        REASON_BASE,
        REASON_MODEL,
        messages,
        temperature=float(payload.get("temperature", 0.6)),
        top_p=float(payload.get("top_p", 0.95)),
        max_tokens=int(payload.get("max_tokens", 2048)),
    )


def decide_orchestrate(payload: dict[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        text = user_text(payload)
        messages = [
            {"role": "system", "content": "You are the main local agentbox model. Be direct and practical."},
            {"role": "user", "content": text},
        ]
    return chat(MAIN_BASE, MAIN_MODEL, messages, max_tokens=int(payload.get("max_tokens", 2048)))


ROUTES = {
    "/context/extract": context_extract,
    "/reason/check": reason_check,
    "/decide/orchestrate": decide_orchestrate,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "agentbox-router/0.1"

    def _send(self, status: int, value: Any) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._send(404, {"error": "not_found"})
            return
        checks: dict[str, Any] = {}
        for name, base in {"main": MAIN_BASE, "context": CONTEXT_BASE, "reason": REASON_BASE}.items():
            try:
                checks[name] = {"ok": True, "models": get_json(f"{base}/models").get("data", [])}
            except Exception as exc:  # noqa: BLE001
                checks[name] = {"ok": False, "error": str(exc)}
        self._send(200, {"ok": all(v["ok"] for v in checks.values()), "checks": checks})

    def do_POST(self) -> None:
        handler = ROUTES.get(self.path)
        if handler is None:
            self._send(404, {"error": "not_found", "routes": sorted(ROUTES)})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size).decode("utf-8") or "{}")
            self._send(200, handler(payload))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            self._send(exc.code, {"error": "upstream_http_error", "detail": body})
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"error": type(exc).__name__, "detail": str(exc)})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)


def main() -> None:
    host = os.environ.get("AGENTBOX_ROUTER_HOST", "127.0.0.1")
    port = int(os.environ.get("AGENTBOX_ROUTER_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"agentbox-router listening on http://{host}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
