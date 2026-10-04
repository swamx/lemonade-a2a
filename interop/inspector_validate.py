"""Check a live adapter with the A2A Inspector's own validators.

    git clone https://github.com/a2aproject/a2a-inspector
    python interop/inspector_validate.py --inspector ../a2a-inspector [--url http://127.0.0.1:9100]

Uses ``backend/validators.py`` from the Inspector (the same functions its UI runs
on the Agent Card and on every response event) over the JSON-RPC and HTTP+JSON
bindings. The Inspector's web UI itself is not driven.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import uuid
from pathlib import Path

import httpx

KIND_BY_WIRE_KEY = {
    "task": "task",
    "message": "message",
    "statusUpdate": "status-update",
    "artifactUpdate": "artifact-update",
}


def load_validators(inspector: Path):
    spec = importlib.util.spec_from_file_location(
        "inspector_validators", inspector / "backend" / "validators.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sse_events(response: httpx.Response):
    for line in response.iter_lines():
        if line.startswith("data:") and line[5:].strip():
            yield json.loads(line[5:])


def stream_events(client: httpx.Client, url: str, binding: str, headers: dict) -> list[dict]:
    message = {"messageId": uuid.uuid4().hex, "role": "ROLE_USER", "parts": [{"text": "hello"}]}
    if binding == "jsonrpc":
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "SendStreamingMessage",
            "params": {"message": message},
        }
        target = url
    else:
        request, target = {"message": message}, f"{url}/message:stream"
    with client.stream("POST", target, json=request, headers=headers) as response:
        response.raise_for_status()
        events = [e.get("result", e) for e in sse_events(response)]
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspector", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:9100")
    parser.add_argument("--auth", default="", help="API key if the adapter enforces one")
    args = parser.parse_args()
    validators = load_validators(args.inspector)
    headers = {"A2A-Version": "1.0"}
    if args.auth:
        headers["Authorization"] = f"Bearer {args.auth}"

    failures = 0
    with httpx.Client(timeout=60) as client:
        card = client.get(f"{args.url}/.well-known/agent-card.json").json()
        errors = validators.validate_agent_card(card)
        print(f"{'PASS' if not errors else 'FAIL'} agent card {errors or ''}")
        failures += bool(errors)

        for binding in ("jsonrpc", "http_json"):
            events = stream_events(client, args.url, binding, headers)
            bad = []
            for event in events:
                ((wire_key, payload),) = event.items()
                data = {**payload, "kind": KIND_BY_WIRE_KEY.get(wire_key, wire_key)}
                bad += [f"{wire_key}: {e}" for e in validators.validate_message(data)]
            kinds = sorted({next(iter(e)) for e in events})
            print(
                f"{'PASS' if not bad else 'FAIL'} {binding} stream: {len(events)} events {kinds} {bad or ''}"
            )
            failures += bool(bad)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
