# Outbound boundary hardening — staged acceptance

The outbound A2A client must rely on upstream SDK protocol negotiation. The
vendor-specific `compat_v03` transport remains temporarily until a real hosted
0.3 peer has passed the equivalent SDK request, response and task tests.

## Implemented

- Shared guarded Agent Card retrieval and explicit host allowlist.
- SDK transport host allowlist, applying to discovery and negotiated endpoints.
- Reject mixed private/public DNS records instead of accepting the first record.
- Reject URL query fragments that could leak credentials or alter endpoint identity.
- Regression tests for mixed DNS, hostile Agent Cards, redirects and allowlists.
- Stable result and exception data types for higher-level orchestration; no
  existing public SDK method signatures were broken.

## Gates before production use

1. Prove TLS certificate hostname validation and SNI against a real HTTPS
   server using pinned IPv4 and IPv6, including mismatch and revoked/expired
   certificates where feasible. The existing mock Host/SNI assertion alone
   cannot prove correctness.
2. Test SDK 0.3 messaging against `travel.agenthaven.dev`, including DataPart
   conversion, no-quote/error replies, and task terminal state. Only then
   remove `client/compat_v03.py`.
3. Provide contract fixtures for JSON-RPC and HTTP+JSON across 0.3 and 1.x
   where supported. Record tested SDK versions, not assumed support.
4. Enforce a single workflow deadline across discovery, connection and
   execution, propagate trace/correlation IDs, and verify cancellation
   and read-only bounded retries under actual connection loss.
5. Add a trusted NANDA discovery provider once all destinations are gated by
   host policy. Do not register loopback URLs as publicly reachable.

## Manual live validation

```bash
python -m pip install -e '.[dev]'
ruff check .
ruff format --check .
pytest -q tests/test_boundary_hardening.py tests/test_client_sdk.py tests/test_ssrf_guard.py
python demo/travel/amd_travel_agent.py --raw-only
```

The last command requires external network access and an available merchant
quota. Do not mistake successful mocked CI for live provider interoperability.
