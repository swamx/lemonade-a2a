"""End-to-end checks against a real Lemonade server (opt-in, see conftest.py).

    LEMONADE_INTEGRATION=1 pytest tests/integration -v

Mirrors what ``scripts/real_lemonade_e2e.py`` and ``benchmarks/cancellation_proof.py``
verify by hand, as repeatable assertions.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

HEADERS = {"A2A-Version": "1.0"}
LONG_PROMPT = (
    "Write a very long, detailed essay (at least 1500 words) about the history of computing."
)


def rpc(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}


def message(text: str) -> dict:
    return {
        "message": {"messageId": uuid.uuid4().hex, "role": "ROLE_USER", "parts": [{"text": text}]}
    }


def artifact_text(task: dict) -> str:
    return "".join(
        part.get("text", "") for art in task.get("artifacts", []) for part in art.get("parts", [])
    )


async def stream_events(client: httpx.AsyncClient, url: str, text: str):
    async with client.stream(
        "POST", url, headers=HEADERS, json=rpc("SendStreamingMessage", message(text))
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                yield json.loads(line[5:])["result"]


async def test_agent_card_is_discoverable_and_streaming(adapter) -> None:
    async with httpx.AsyncClient() as client:
        card = (await client.get(f"{adapter.url}/.well-known/agent-card.json")).json()

    assert card["capabilities"]["streaming"] is True
    assert {i["protocolBinding"] for i in card["supportedInterfaces"]} >= {"JSONRPC", "HTTP+JSON"}


async def test_send_message_returns_model_text_as_an_artifact(adapter) -> None:
    async with httpx.AsyncClient(timeout=300) as client:
        reply = await client.post(
            adapter.url, headers=HEADERS, json=rpc("SendMessage", message("Say hello briefly."))
        )

    task = reply.json()["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert artifact_text(task).strip(), "the real model produced no text"


async def test_streaming_delivers_incremental_chunks_then_completes(adapter) -> None:
    chunks, final = 0, None
    async with httpx.AsyncClient(timeout=300) as client:
        async for event in stream_events(client, adapter.url, "Count from one to ten in words."):
            chunks += "artifactUpdate" in event
            if "statusUpdate" in event:
                final = event["statusUpdate"]["status"]["state"]

    assert chunks >= 2, "text should arrive in several chunks, not one blob"
    assert final == "TASK_STATE_COMPLETED"


async def test_http_json_binding_also_works(adapter) -> None:
    async with httpx.AsyncClient(timeout=300) as client:
        reply = await client.post(
            f"{adapter.url}/message:send", headers=HEADERS, json=message("Say hi.")
        )

    assert reply.status_code == 200
    assert reply.json()["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


async def _small_direct_request_ms(client: httpx.AsyncClient, adapter) -> float:
    start = time.perf_counter()
    response = await client.post(
        f"{adapter.lemonade_url}/chat/completions",
        json={
            "model": adapter.model,
            "messages": [{"role": "user", "content": "Say OK"}],
            "max_tokens": 3,
        },
    )
    response.raise_for_status()
    return (time.perf_counter() - start) * 1000


async def test_cancel_stops_generation_on_the_real_backend(adapter) -> None:
    """A tiny request must not queue behind a cancelled generation."""
    async with httpx.AsyncClient(timeout=600) as client:
        await _small_direct_request_ms(client, adapter)  # make sure the model is loaded
        idle = await _small_direct_request_ms(client, adapter)

        task_id = None
        stream = stream_events(client, adapter.url, LONG_PROMPT)
        async for event in stream:
            task_id = task_id or (event.get("task") or event.get("statusUpdate") or {}).get(
                "id" if "task" in event else "taskId"
            )
            if "artifactUpdate" in event:
                break
        assert task_id, "no task id seen on the stream"

        canceled = await client.post(
            adapter.url, headers=HEADERS, json=rpc("CancelTask", {"id": task_id})
        )
        assert canceled.json()["result"]["status"]["state"] == "TASK_STATE_CANCELED"
        await stream.aclose()
        await asyncio.sleep(0.5)

        after = await _small_direct_request_ms(client, adapter)

    # Queued behind a still-running essay this would take many seconds.
    assert after < max(10 * idle, 3000), f"backend still busy after cancel: {after:.0f} ms"


async def test_get_task_after_completion_returns_the_same_task(adapter) -> None:
    async with httpx.AsyncClient(timeout=300) as client:
        sent = await client.post(
            adapter.url, headers=HEADERS, json=rpc("SendMessage", message("Say OK."))
        )
        task_id = sent.json()["result"]["task"]["id"]
        fetched = await client.post(
            adapter.url, headers=HEADERS, json=rpc("GetTask", {"id": task_id})
        )

    assert fetched.json()["result"]["id"] == task_id
