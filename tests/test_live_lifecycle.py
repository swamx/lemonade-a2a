"""Lifecycle behaviour that only shows up on real connections.

Slow-consumer backpressure, client disconnect (default and opt-in cancel) and
repeated cancel races, against a mock Lemonade and the real adapter over sockets.
"""

from __future__ import annotations

import socket
import time

import httpx
import pytest

from tests.live import A2A_HEADERS, live_adapter, message_body, sse_events, wait_for_state

TERMINAL = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_REJECTED",
}


@pytest.fixture
def slow_backend(monkeypatch):
    """About 2 s of output: 100 tokens, 20 ms apart."""
    monkeypatch.setenv("MOCK_LEMONADE_TOKEN_COUNT", "100")
    monkeypatch.setenv("MOCK_LEMONADE_TOKEN_DELAY", "0.02")


def _first_task_id(events) -> str:
    """Task id from the first ``task`` event; ``events`` must stay referenced by the caller
    (dropping an httpx line iterator closes its connection)."""
    for event in events:
        task = event.get("task")
        if task:
            return task["id"]
    raise AssertionError("stream ended without a task")


def test_stalled_streaming_client_cannot_wedge_the_adapter(monkeypatch) -> None:
    """A client that connects and never reads is bounded by the queue and the task deadline."""
    monkeypatch.setenv("MOCK_LEMONADE_TOKEN_COUNT", "300000")
    monkeypatch.setenv("MOCK_LEMONADE_TOKEN_DELAY", "0")
    with live_adapter(max_task_seconds=2) as base:
        port = int(base.rsplit(":", 1)[1])
        body = message_body()
        stalled = socket.socket()
        stalled.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        stalled.connect(("127.0.0.1", port))
        stalled.sendall(
            (
                "POST /message:stream HTTP/1.1\r\nHost: x\r\nA2A-Version: 1.0\r\n"
                f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n{body}"
            ).encode()
        )
        try:
            with httpx.Client() as client:
                time.sleep(0.5)
                # The adapter keeps answering other callers while one consumer is stuck...
                assert client.get(f"{base}/healthz").status_code == 200
                tasks = client.get(f"{base}/tasks", headers=A2A_HEADERS).json()["tasks"]
                assert len(tasks) == 1
                # ...and the stuck task ends at its deadline instead of hanging.
                state = wait_for_state(client, base, tasks[0]["id"], TERMINAL, timeout=10)
                assert state == "TASK_STATE_FAILED"
        finally:
            stalled.close()


def test_disconnect_does_not_cancel_by_default(slow_backend) -> None:
    with live_adapter() as base, httpx.Client(timeout=10) as client:
        with client.stream(
            "POST", f"{base}/message:stream", headers=A2A_HEADERS, content=message_body()
        ) as response:
            task_id = _first_task_id(sse_events(response))
        # The connection is closed; the task carries on and finishes.
        assert wait_for_state(client, base, task_id, TERMINAL) == "TASK_STATE_COMPLETED"


def test_cancel_on_disconnect_cancels_the_task(monkeypatch) -> None:
    # About 6 s of output, so "cancelled within 3 s" cannot be mistaken for "finished".
    monkeypatch.setenv("MOCK_LEMONADE_TOKEN_COUNT", "300")
    monkeypatch.setenv("MOCK_LEMONADE_TOKEN_DELAY", "0.02")
    with live_adapter(cancel_on_disconnect=True) as base, httpx.Client(timeout=10) as client:
        with client.stream(
            "POST", f"{base}/message:stream", headers=A2A_HEADERS, content=message_body()
        ) as response:
            task_id = _first_task_id(sse_events(response))
        assert wait_for_state(client, base, task_id, TERMINAL, timeout=3) == "TASK_STATE_CANCELED"


def test_cancel_on_disconnect_ignores_a_subscriber_leaving(slow_backend) -> None:
    """Only the request that started the task counts; a resubscriber going away does not."""
    with (
        live_adapter(cancel_on_disconnect=True) as base,
        httpx.Client(timeout=10) as client,
        client.stream(
            "POST", f"{base}/message:stream", headers=A2A_HEADERS, content=message_body()
        ) as starter,
    ):
        starter_events = sse_events(starter)
        task_id = _first_task_id(starter_events)
        with client.stream(
            "POST", f"{base}/tasks/{task_id}:subscribe", headers=A2A_HEADERS
        ) as subscriber:
            subscriber_events = sse_events(subscriber)
            next(subscriber_events)
        # The subscriber has gone; the starter is still connected.
        time.sleep(0.3)
        task = client.get(f"{base}/tasks/{task_id}", headers=A2A_HEADERS).json()
        assert task["status"]["state"] in {"TASK_STATE_WORKING", "TASK_STATE_COMPLETED"}


def test_repeated_cancel_races_leave_no_task_running(monkeypatch) -> None:
    """Cancel at random moments, twice each: every task ends terminal and nothing leaks."""
    monkeypatch.setenv("MOCK_LEMONADE_TOKEN_COUNT", "40")
    monkeypatch.setenv("MOCK_LEMONADE_TOKEN_DELAY", "0.01")
    delays = [0, 0.005, 0.02, 0.05, 0.1, 0.2, 0.4, 0.6]
    with live_adapter(max_concurrent_tasks=64) as base, httpx.Client(timeout=15) as client:
        ids = []
        for index, delay in enumerate(delays * 3):
            with client.stream(
                "POST",
                f"{base}/message:stream",
                headers=A2A_HEADERS,
                content=message_body(message_id=f"race-{index}"),
            ) as response:
                task_id = _first_task_id(sse_events(response))
            ids.append(task_id)
            time.sleep(delay)
            first = client.post(f"{base}/tasks/{task_id}:cancel", headers=A2A_HEADERS)
            second = client.post(f"{base}/tasks/{task_id}:cancel", headers=A2A_HEADERS)
            # Cancelling again, or after completion, is a clean A2A error, never a 5xx.
            assert first.status_code < 500 and second.status_code < 500
        states = {wait_for_state(client, base, task_id, TERMINAL, timeout=10) for task_id in ids}
        assert states <= {"TASK_STATE_CANCELED", "TASK_STATE_COMPLETED"}, states
        assert "TASK_STATE_CANCELED" in states
