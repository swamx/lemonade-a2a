"""Does a client disconnect free the real Lemonade backend? Default vs ``cancel_on_disconnect``.

    python benchmarks/disconnect_proof.py --model Gemma-3-4b-it-GGUF [--output result.json]

Starts the adapter in-process against a real Lemonade, streams a long generation, drops
the connection after the first artifact chunk, then times a tiny direct request. If the
backend is still busy with the abandoned generation the tiny request queues behind it
(Lemonade/llama.cpp serves one request at a time); if the task was cancelled it is served
at idle speed. Run from the repository root.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lemonade_a2a.config import Settings
from lemonade_a2a.server import create_app
from tests.live import A2A_HEADERS, free_port, serve, sse_events

PROMPT = (
    "Write a detailed, well-structured explanation of about 600 words on how local AI "
    "inference works."
)


def tiny_request_ms(lemonade: str, model: str) -> float:
    start = time.perf_counter()
    httpx.post(
        f"{lemonade}/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 4},
        timeout=300,
    )
    return (time.perf_counter() - start) * 1000


def trial(lemonade: str, model: str, *, cancel_on_disconnect: bool) -> dict:
    port = free_port()
    settings = Settings(
        lemonade_base_url=lemonade,
        model=model,
        port=port,
        public_url=f"http://127.0.0.1:{port}",
        cancel_on_disconnect=cancel_on_disconnect,
    )
    with serve(create_app(settings), port), httpx.Client(timeout=300) as client:
        base = f"http://127.0.0.1:{port}"
        message = {
            "messageId": "disconnect-proof",
            "role": "ROLE_USER",
            "parts": [{"text": PROMPT}],
        }
        task_id = None
        with client.stream(
            "POST",
            f"{base}/message:stream",
            headers=A2A_HEADERS,
            content=json.dumps({"message": message}),
        ) as response:
            for event in sse_events(response):
                task_id = (event.get("task") or {}).get("id", task_id)
                if "artifactUpdate" in event:
                    break
        # The connection is closed on leaving the block above.
        waited = tiny_request_ms(lemonade, model)
        state = client.get(f"{base}/tasks/{task_id}", headers=A2A_HEADERS).json()["status"]["state"]
    return {
        "cancel_on_disconnect": cancel_on_disconnect,
        "small_request_after_disconnect_ms": round(waited),
        "task_state_after": state,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--lemonade-url", default="http://127.0.0.1:13305/v1")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    lemonade = args.lemonade_url.rstrip("/")
    tiny_request_ms(lemonade, args.model)  # load the model
    idle = round(min(tiny_request_ms(lemonade, args.model) for _ in range(3)))
    result = {
        "model": args.model,
        "idle_small_request_ms": idle,
        "default": trial(lemonade, args.model, cancel_on_disconnect=False),
        "cancel_on_disconnect": trial(lemonade, args.model, cancel_on_disconnect=True),
    }
    print(json.dumps(result, indent=2))
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    freed = result["cancel_on_disconnect"]["task_state_after"] == "TASK_STATE_CANCELED"
    return 0 if freed else 1


if __name__ == "__main__":
    raise SystemExit(main())
