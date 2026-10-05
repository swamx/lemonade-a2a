"""Prove that A2A ``CancelTask`` stops generation on the real Lemonade backend.

Lemonade (llama.cpp) serves requests one at a time, so an unfinished generation
makes any other request queue behind it. The script times a tiny direct request

1. on an idle server (baseline),
2. while a long A2A-started generation is still running (it queues: control),
3. right after ``CancelTask`` on such a generation (it must be back to baseline).

If cancellation only changed task metadata, step 3 would look like step 2.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid

import httpx

A2A_HEADERS = {"A2A-Version": "1.0"}
ESSAY = "Write a very long, detailed essay (at least 1500 words) about the history of computing."


def rpc(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}


async def direct_small(client: httpx.AsyncClient, args: argparse.Namespace) -> float:
    start = time.perf_counter()
    response = await client.post(
        args.direct_url,
        json={
            "model": args.model,
            "messages": [{"role": "user", "content": "Say OK"}],
            "max_tokens": 3,
        },
    )
    response.raise_for_status()
    return (time.perf_counter() - start) * 1000


async def start_essay(client: httpx.AsyncClient, args: argparse.Namespace):
    """Open an A2A stream and return it once text is flowing, with the task id."""
    message = {
        "message": {
            "messageId": uuid.uuid4().hex,
            "role": "ROLE_USER",
            "parts": [{"text": ESSAY}],
        }
    }
    stream = client.stream(
        "POST", args.a2a_url, headers=A2A_HEADERS, json=rpc("SendStreamingMessage", message)
    )
    response = await stream.__aenter__()
    task_id = None
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        result = json.loads(line[5:])["result"]
        task_id = (
            task_id
            or (result.get("task") or {}).get("id")
            or (result.get("statusUpdate") or {}).get("taskId")
        )
        if "artifactUpdate" in result:
            break
    return stream, task_id


async def cancel(client: httpx.AsyncClient, args: argparse.Namespace, task_id: str) -> str:
    reply = await client.post(
        args.a2a_url, headers=A2A_HEADERS, json=rpc("CancelTask", {"id": task_id})
    )
    body = reply.json()
    if "error" in body:  # e.g. the generation already finished
        return f"error: {body['error'].get('message')}"
    return body["result"]["status"]["state"]


async def main(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        await direct_small(client, args)  # load the model
        idle = await direct_small(client, args)

        stream, task_id = await start_essay(client, args)
        await asyncio.sleep(0.5)
        try:  # normally waits for the essay to finish; a rambling model may not
            queued = await asyncio.wait_for(direct_small(client, args), args.control_timeout)
            queued_note = ""
        except TimeoutError:
            queued, queued_note = args.control_timeout * 1000, " (at least; gave up waiting)"
        await cancel(client, args, task_id)
        await stream.__aexit__(None, None, None)
        await asyncio.sleep(1)

        stream, task_id = await start_essay(client, args)
        await asyncio.sleep(0.5)
        state = await cancel(client, args, task_id)
        after = await direct_small(client, args)
        await stream.__aexit__(None, None, None)

    print(f"idle small request:                          {idle:8.0f} ms")
    print(f"small request behind a running generation:   {queued:8.0f} ms{queued_note}")
    print(f"CancelTask -> {state}; small request after:  {after:8.0f} ms")
    stopped = after < max(5 * idle, 1000) and after < queued / 2
    print("RESULT:", "PASS - backend generation stopped" if stopped else "FAIL/INCONCLUSIVE")
    raise SystemExit(0 if stopped else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a2a-url", default="http://127.0.0.1:9100")
    parser.add_argument("--direct-url", default="http://127.0.0.1:13305/v1/chat/completions")
    parser.add_argument("--model", default=os.getenv("LEMONADE_MODEL", ""))
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--control-timeout", type=float, default=60.0, help="seconds to wait in the queued control"
    )
    parsed = parser.parse_args()
    if not parsed.model:
        parser.error("--model is required unless LEMONADE_MODEL is set")
    asyncio.run(main(parsed))
