"""Local-first A2A demo: discover a Lemonade agent, send a message, show output.

This is also usable behind an optional NANDA resolver: resolve an agent ID to
a trusted Agent Card URL first, then pass its LOCAL URL via --base-url.
No remote registry is required for the offline path.

Run: python scripts/demo_local_a2a.py --base-url http://127.0.0.1:9100
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from urllib.parse import urlparse

import httpx


def local_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise argparse.ArgumentTypeError("The local demo only accepts loopback HTTP URLs")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise argparse.ArgumentTypeError("URL must not contain credentials, query or fragment")
    return value.rstrip("/")


def discover(client: httpx.Client, base: str) -> dict:
    # A2A v1 well-known card path; do not silently ignore unexpected HTTP errors.
    response = client.get(base + "/.well-known/agent-card.json")
    response.raise_for_status()
    card = response.json()
    if not isinstance(card, dict) or not card.get("name"):
        raise ValueError("Invalid Agent Card")
    return card


def send(client: httpx.Client, base: str, prompt: str, token: str | None) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "ROLE_USER",
                "parts": [{"text": prompt}],
            }
        },
    }
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.post(base, json=payload, headers=headers)
    response.raise_for_status()
    result = response.json()
    if "error" in result:
        raise RuntimeError(f"A2A error: {result['error']}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", type=local_url, default="http://127.0.0.1:9100")
    parser.add_argument(
        "--prompt", default="Explain how local A2A agents protect private data in two sentences."
    )
    parser.add_argument("--token", help="Optional bearer token for authenticated local adapter")
    args = parser.parse_args()
    try:
        with httpx.Client(timeout=180, trust_env=False) as client:
            card = discover(client, args.base_url)
            print("DISCOVERED:", card["name"])
            print("SKILLS:", [skill.get("id") for skill in card.get("skills", [])])
            print("ENDPOINT:", args.base_url)
            response = send(client, args.base_url, args.prompt, args.token)
            print("A2A RESPONSE:")
            print(json.dumps(response, indent=2))
        return 0
    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        print(f"Demo failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
