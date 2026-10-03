from __future__ import annotations

import argparse
import asyncio
import json
import uuid

import httpx


def collect_text(value) -> str:
    pieces: list[str] = []
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            pieces.append(text)
        for child in value.values():
            pieces.append(collect_text(child))
    elif isinstance(value, list):
        for child in value:
            pieces.append(collect_text(child))
    return "".join(pieces)


async def wait_ready(client: httpx.AsyncClient, url: str, attempts: int = 120) -> None:
    for _ in range(attempts):
        try:
            response = await client.get(url)
            if response.status_code < 500:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError(f"service did not become ready: {url}")


async def discover_model(client: httpx.AsyncClient, lemonade: str) -> str:
    response = await client.get(f"{lemonade.rstrip('/')}/models")
    response.raise_for_status()
    data = response.json().get("data") or []
    if not data:
        raise RuntimeError("Lemonade returned no models from /v1/models")
    model = data[0].get("id")
    if not isinstance(model, str) or not model:
        raise RuntimeError("Lemonade /v1/models did not return a usable model id")
    return model


async def main(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        await wait_ready(client, f"{args.a2a.rstrip('/')}/healthz")
        await wait_ready(client, f"{args.lemonade.rstrip('/')}/models")

        model = args.model or await discover_model(client, args.lemonade)
        print(f"Using Lemonade model: {model}")

        card = (await client.get(f"{args.a2a.rstrip('/')}/.well-known/agent-card.json")).json()
        if not card.get("name"):
            raise RuntimeError("Agent Card is missing name")

        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "SendMessage",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "role": "ROLE_USER",
                    "parts": [{"kind": "text", "text": args.prompt}],
                }
            },
        }
        response = await client.post(
            f"{args.a2a.rstrip('/')}/a2a/jsonrpc",
            headers={"A2A-Version": "1.0"},
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            raise RuntimeError(json.dumps(body["error"], indent=2))
        text = collect_text(body.get("result")).strip()
        if not text:
            raise AssertionError(f"A2A returned no model text: {body!r}")
        print(f"PASS: real Lemonade -> A2A artifact ({len(text)} chars)")
        print(text[:500])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate Lemonade A2A against a real Lemonade server")
    parser.add_argument("--a2a", default="http://127.0.0.1:9000")
    parser.add_argument("--lemonade", default="http://127.0.0.1:13305/v1")
    parser.add_argument("--model", default="")
    parser.add_argument("--prompt", default="Reply in one short sentence: what is local AI?")
    parser.add_argument("--timeout", type=float, default=300.0)
    asyncio.run(main(parser.parse_args()))
