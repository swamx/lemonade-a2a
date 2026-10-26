# AMD Lemonade Challenge: Agent Bench Travel Merchant

This is a **real outbound A2A request** to an independently hosted third-party
agent, followed by a local Lemonade summarization. Checkout is deliberately
excluded because it requires an authorization mandate and does **not** issue
flight tickets. A quoted itinerary is **not** an actual booking.

## Prerequisites

- Python 3.11+, internet access for remote merchant requests.
- Running Lemonade server with a loaded text model (default URL from this repo
  is `http://127.0.0.1:13305/v1`).
- Current release of this package installed in editable mode.

```bash
python -m pip install -e ".[dev]"
# First verify remote protocol without Lemonade:
python demo/travel/amd_travel_agent.py --origin MAD --destination LHR --date 2026-11-19 --raw-only

# Then run complete demo against the locally loaded model:
python demo/travel/amd_travel_agent.py --origin MAD --destination LHR --date 2026-11-19
```

Set `LEMONADE_MODEL` if more than one model is exposed. Override
`LEMONADE_URL` if the local server uses a different address.

## Evidence to capture in screen recording

1. The vendor-hosted Agent Card:
   `https://travel.agenthaven.dev/.well-known/agent-card.json`
2. The request logs: `message/send` with `DataPart` containing
   `{"action":"search_flights","input":{"origin":"MAD","destination":"LHR","departure_date":"2026-11-19"}}`.
3. The actual remote JSON-RPC response, printed unmodified.
4. Locally generated Lemonade comparison of the options.
5. Distinguish the two inference/transport boundaries (the merchant returns
   external data, Lemonade summarizes locally).
6. Document any errors or unavailability plainly; never substitute an invented quote.

## Protocol notes

The Agent Bench card currently advertises A2A 0.3 JSON-RPC, while this repo's
A2A server uses a 1.2.x Python SDK. The optional `client.compat_v03.RemoteA2AClient`
sends explicit 0.3 wire-format requests so no incompatible 1.x client calls
are assumed to work. The provider's discovery identity and exact endpoint are
pinned in the example rather than resolved from an arbitrary URL.

The external merchant documents a read-only anonymous `search_flights` skill,
returning at most two short-lived quotes in EUR minor units. A checkout may
require a bearer token and spending mandate and is not part of this script.

## Demo talk track (4–5 min)

- 0:00 Problem: private AI inference is not enough for cross-organization action.
- 0:40 AMD Lemonade serves the local reasoning model.
- 1:20 Open the remote Agent Card to demonstrate independently hosted skills.
- 2:00 Send `message/send`, show the real returned quote and evidence.
- 3:10 Show Lemonade comparing cheapest versus fastest in human language.
- 4:00 Explain SDK version boundaries, no purchased tickets, and next steps.

## Repeatability and limitations

Endpoint health reported by external directories is not a guarantee that a
live call will succeed. Run `--raw-only` before recording. Do not cache live
quotes as though they are current. Flight search input requires IATA airport
codes and a future ISO date. Remote merchant may not cover all route/date pairs.
