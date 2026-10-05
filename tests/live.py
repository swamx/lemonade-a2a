"""Helpers for tests that need real sockets: a mock Lemonade plus the adapter on free ports."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn

from lemonade_a2a.config import Settings
from lemonade_a2a.server import create_app
from tests.mock_lemonade import app as mock_app

A2A_HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def serve(app, port: int, **uvicorn_kwargs) -> Iterator[uvicorn.Server]:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", **uvicorn_kwargs)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("test server did not start")
        time.sleep(0.02)
    try:
        yield server
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@contextmanager
def live_adapter(**settings) -> Iterator[str]:
    """Mock Lemonade + adapter; yields the adapter's base URL."""
    mock_port, port = free_port(), free_port()
    with serve(mock_app, mock_port):
        config = Settings(
            lemonade_base_url=f"http://127.0.0.1:{mock_port}/v1",
            port=port,
            public_url=f"http://127.0.0.1:{port}",
            **settings,
        )
        with serve(create_app(config), port):
            yield f"http://127.0.0.1:{port}"


def message_body(text: str = "go", message_id: str = "m1") -> str:
    return json.dumps(
        {"message": {"messageId": message_id, "role": "ROLE_USER", "parts": [{"text": text}]}}
    )


def sse_events(response) -> Iterator[dict]:
    """Decoded ``data:`` payloads of a streaming httpx response."""
    for line in response.iter_lines():
        if line.startswith("data:"):
            yield json.loads(line[5:].strip())


def wait_for_state(client, base: str, task_id: str, wanted: set[str], timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    state = ""
    while time.monotonic() < deadline:
        task = client.get(f"{base}/tasks/{task_id}", headers=A2A_HEADERS).json()
        state = task.get("status", {}).get("state", "")
        if state in wanted:
            return state
        time.sleep(0.05)
    return state
