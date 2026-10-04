"""Compare direct Lemonade latency with the A2A protocol path.

This benchmark intentionally reports protocol overhead separately from model
quality. Run a Lemonade server and lemonade-a2a using the same backend/model.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import time
import uuid

import httpx


async def timed_post(client: httpx.AsyncClient, url: str, payload: dict, headers=None) -> float:
    start = time.perf_counter()
    response = await client.post(url, json=payload, headers=headers)
    response.raise_for_status()
    _ = response.content
    return (time.perf_counter() - start) * 1000


async def run(args: argparse.Namespace) -> None:
    direct_payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
        "stream": False,
    }
    a2a_payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "ROLE_USER",
                "parts": [{"text": args.prompt}],
            }
        },
    }

    direct: list[float] = []
    a2a: list[float] = []
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        for _ in range(args.warmup):
            await timed_post(client, args.direct_url, direct_payload)
            await timed_post(client, args.a2a_url, a2a_payload, {"A2A-Version": "1.0"})
        for _ in range(args.runs):
            direct.append(await timed_post(client, args.direct_url, direct_payload))
            a2a.append(await timed_post(client, args.a2a_url, a2a_payload, {"A2A-Version": "1.0"}))

    direct_med = statistics.median(direct)
    a2a_med = statistics.median(a2a)
    print(f"direct median: {direct_med:.2f} ms")
    print(f"A2A median:    {a2a_med:.2f} ms")
    print(f"A2A overhead:  {a2a_med - direct_med:.2f} ms")
    print(f"overhead pct:  {((a2a_med / direct_med) - 1) * 100:.2f}%")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--direct-url", default="http://127.0.0.1:13305/v1/chat/completions")
    parser.add_argument("--a2a-url", default="http://127.0.0.1:9100")
    parser.add_argument(
        "--model",
        default=os.getenv("LEMONADE_MODEL", ""),
        help="Model ID; must match LEMONADE_MODEL used by the running A2A adapter.",
    )
    parser.add_argument("--prompt", default="Reply with exactly: OK")
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    if not args.model:
        parser.error("--model is required unless LEMONADE_MODEL is set")
    if args.runs < 1 or args.warmup < 0:
        parser.error("--runs must be positive and --warmup must be non-negative")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
