"""AMD Challenge demo: local Lemonade summarizes a real remote A2A flight quote.

Usage:
  python demo/travel/amd_travel_agent.py --origin MAD --destination LHR --date 2026-11-19

This script sends an actual message/send call to the independently hosted
Agent Bench Travel Merchant, then calls Lemonade's local OpenAI-compatible API.
It does not perform checkout, purchase tickets or fabricate quotes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date

import httpx

from lemonade_a2a.client.compat_v03 import RemoteA2AClient
from lemonade_a2a.discovery.cards import fetch_agent_card
from lemonade_a2a.lemonade_client import LemonadeClient

CARD = "https://travel.agenthaven.dev/.well-known/agent-card.json"
ENDPOINT = "https://travel.agenthaven.dev/a2a"


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Local Lemonade -> Remote Agent Bench Travel Merchant"
    )
    parser.add_argument("--origin", default="MAD", help="Three-letter IATA departure code")
    parser.add_argument("--destination", default="LHR", help="Three-letter IATA arrival code")
    parser.add_argument("--date", default="2026-11-19", help="Departure date YYYY-MM-DD")
    parser.add_argument("--model", default=os.getenv("LEMONADE_MODEL", ""))
    parser.add_argument(
        "--lemonade-url", default=os.getenv("LEMONADE_URL", "http://127.0.0.1:13305/v1")
    )
    parser.add_argument("--raw-only", action="store_true", help="Only test the remote A2A agent")
    args = parser.parse_args()
    for code in (args.origin, args.destination):
        if len(code) != 3 or not code.isalpha() or code != code.upper():
            parser.error("IATA codes must be 3 uppercase letters (e.g., MAD or LHR)")
    try:
        when = date.fromisoformat(args.date)
    except ValueError:
        parser.error("Date must be YYYY-MM-DD")
    if when < date.today():
        parser.error("Departure date must not be in the past")

    try:
        card = await fetch_agent_card(CARD, allowed_hosts=frozenset({"travel.agenthaven.dev"}))
        print("REMOTE AGENT:", card.get("name", "unnamed"))
        print("REMOTE A2A:", ENDPOINT)
        print("ADVERTISED SKILLS:", [s.get("id") for s in card.get("skills", [])])
        if not any(s.get("id") == "flight-search" for s in card.get("skills", [])):
            raise ValueError("Remote agent no longer advertises flight-search")
        print(f"\nA2A message/send: {args.origin} -> {args.destination} on {args.date}")
        remote = RemoteA2AClient(ENDPOINT)
        result = await remote.send_data(
            "search_flights",
            {
                "origin": args.origin,
                "destination": args.destination,
                "departure_date": args.date,
            },
        )
        print("\nREMOTE A2A RESULT:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if args.raw_only:
            return 0

        llm = LemonadeClient(base_url=args.lemonade_url, model=args.model)
        try:
            answer = await llm.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are a travel research assistant running LOCALLY on AMD Lemonade. "
                            "Summarize the remote A2A quote accurately. Never invent flights, "
                            "prices, airline names or ticket reservations. If the remote result "
                            "contains fares in minor units, divide by 100 and retain currency. "
                            "There are no purchases or ticket bookings in this demo."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Explain these remote flight search results for {args.origin} to "
                            f"{args.destination} on {args.date}. Clearly distinguish cheapest "
                            f"from fastest if possible:\n{json.dumps(result)}"
                        ),
                    },
                ]
            )
            print("\nLOCAL LEMONADE SUMMARY:\n" + answer)
        finally:
            await llm.aclose()
        return 0
    except (httpx.HTTPError, ValueError, TypeError, RuntimeError, KeyError, IndexError) as exc:
        print(f"Demo failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
