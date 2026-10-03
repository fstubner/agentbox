"""A minimal RFC 6455 WebSocket client.

eufy-security-ws only speaks WebSocket, and the bridges use only the standard
library, so each one can be read end to end. A process that holds a credential
should not bring a dependency tree nobody audits.

It does only what a client needs: the upgrade handshake, masked writes,
unmasked reads, close and ping.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import struct
import threading
import urllib.parse

TEXT, BINARY, CLOSE, PING, PONG = 0x1, 0x2, 0x8, 0x9, 0xA


class WebSocketError(Exception):
    pass


class WebSocket:
    """One connection. Not thread-safe for writes; guard with the lock."""

    def __init__(self, url: str, timeout: float = 20.0):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "ws":
            raise WebSocketError("only ws:// is supported; this talks to a "
                                 "container on a private docker network")
        self.lock = threading.Lock()
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(request.encode())
        response = self._read_until(b"\r\n\r\n")
        if b" 101 " not in response.split(b"\r\n")[0]:
            raise WebSocketError(f"upgrade refused: "
                                 f"{response.split(chr(13).encode())[0][:80]!r}")

    def _read_until(self, marker: bytes) -> bytes:
        buffer = b""
        while marker not in buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WebSocketError("connection closed during handshake")
            buffer += chunk
            if len(buffer) > 65536:
                raise WebSocketError("handshake response too large")
        return buffer

    def _recv_exact(self, count: int) -> bytes:
        out = b""
        while len(out) < count:
            chunk = self.sock.recv(count - len(out))
            if not chunk:
                raise WebSocketError("connection closed")
            out += chunk
        return out

    def send_json(self, payload: dict) -> None:
        data = json.dumps(payload).encode()
        # RFC 6455 requires every client frame to be masked with an
        # unpredictable mask.
        mask = secrets.token_bytes(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        header = bytes([0x80 | TEXT])
        length = len(data)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", length)
        with self.lock:
            self.sock.sendall(header + mask + masked)

    def recv(self) -> dict | None:
        """The next application message, or None once the peer closes.

        Control frames are handled here, so a caller waiting for an event never
        sees a ping.
        """
        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            if length > 8 * 1024 * 1024:
                raise WebSocketError("frame too large")
            masked = bool(second & 0x80)
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length) if length else b""
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == CLOSE:
                return None
            if opcode == PING:
                with self.lock:
                    m = secrets.token_bytes(4)
                    body = bytes(b ^ m[i % 4] for i, b in enumerate(payload))
                    self.sock.sendall(bytes([0x80 | PONG, 0x80 | len(payload)])
                                      + m + body)
                continue
            if opcode == PONG:
                continue
            if opcode == TEXT:
                try:
                    return json.loads(payload.decode())
                except (ValueError, UnicodeDecodeError):
                    raise WebSocketError("non-JSON text frame") from None
            # Binary frames are video. This bridge carries none, so they are
            # dropped rather than buffered.

    def close(self) -> None:
        try:
            with self.lock:
                self.sock.sendall(bytes([0x80 | CLOSE, 0x80]) + secrets.token_bytes(4))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def connect(url: str = "", timeout: float = 20.0) -> WebSocket:
    return WebSocket(url or os.environ.get("EUFY_WS_URL",
                                           "ws://eufy-security-ws:3000"), timeout)
