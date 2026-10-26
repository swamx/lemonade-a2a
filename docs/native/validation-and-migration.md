# Validation, telemetry mapping and rollout

**Status: draft (2026-10-15).** The adapter is the executable specification: every behavior is ported only after a
black-box test exists for it ([upstream-integration.md](../upstream-integration.md)). This document says how those
tests run against a native build and in what order the work lands.

## Gates, and what each needs to run against a native URL

| Gate | Today | Change needed for native |
|---|---|---|
| Official A2A TCK | `scripts/run_tck.py` starts the Python test SUT, then runs the TCK against it | **URL mode exists:** `run_tck.py --url http://host:port` skips the SUT (verified against the Python test SUT started by hand: 157 passed, 4 expected-fail, unpatched). The SUT exists only because the TCK needs scenario prompts answered deterministically; native needs the same, either a deterministic mock model behind `lemond` (as `tests/mock_lemonade.py` does for the adapter) or a recorded-prompt mode. That remains the largest open item (spike S3) |
| Black-box end-to-end | `scripts/blackbox_e2e.py` (`A2A_BASE_URL`, readiness via `/healthz`) | **Done:** the readiness path is configurable (`A2A_READY_PATH`, `--ready-path`); native has its own liveness endpoint (*verified*: `/live`) |
| Independent clients (A2A CLI, JS, Go, .NET, Java SDKs, Inspector) | `interop/`, 10 of 10 per binding with API-key auth | None beyond the URL and the key; these discover through the Agent Card |
| Backend contract fixtures | `tests/contract/` replays recorded Lemonade streams into the *Python* client | Reuse the recordings as vectors for the native stream bridge's parser (a C++ test fed the same files, asserting the same deltas and errors); the expectation format is documented for non-Python consumers in `tests/contract/fixtures/README.md` |
| Behavior unit tests | pytest | Port the table in [protocol-mapping.md](../protocol-mapping.md) as upstream-convention tests (`test/cpp`, `cpp-ci`; and a `test/server_a2a.py` beside `test/server_mcp.py`, *verified* naming) |
| Fuzzing | `tests/test_fuzz.py` (Hypothesis) | Run the same generators against the native URL from Python; no C++ port needed |
| Cancellation proof, disconnect proof | `benchmarks/cancellation_proof.py`, `disconnect_proof.py` | URL only |
| Benchmarks | `benchmarks/benchmark_evidence.py` direct versus adapter | Add a third target (native) to the same harness; output tokens/s is now the comparable throughput, not chunks/s ([benchmarks.md](../benchmarks.md)) |

Native results are recorded in the same files and formats (`conformance-results.json`, `docs/benchmark-results/`)
with an `implementation` field, so the two are compared like for like.

## Telemetry mapping (decision D6)

The adapter's instrument names are implementation-independent by design ([observability.md](../observability.md) §2).
Native differs in one way: it *is* the server, so the adapter's `gen_ai.client.*` instruments (it is a GenAI client
of Lemonade) have no counterpart; generation itself is already traced and measured by upstream's own telemetry
(OTLP with GenAI semantics, trace-context ingestion, `/metrics`, `/api/v1/stats`; see §8 there).

| Concern | Proposal |
|---|---|
| Task and stream instruments (`lemonade_a2a.task.*`, `stream.ttft`, `stream.chunk_interval`, `store.*`, `disconnects`) | Emit the same names through upstream's telemetry facility, so dashboards and alerts keep working |
| `backend.*`, `compat.status`, `gen_ai.client.*` | Not applicable natively (no network backend; no compat check; generation is upstream's own span) |
| Trace shape | `a2a.task.execute` becomes a child of the request span and the parent of upstream's generation span, so one trace runs client to token |
| Privacy | Same modes: no prompt or response text in any signal by default |

Whether upstream's telemetry layer is a good home for new instruments is for its maintainers to say (*assumed*:
check how they are registered).

## Overhead expectations

The in-process path removes the HTTP hop and the Python layer. The adapter's measured cost (about 5-15 ms of
time-to-first-token, flat throughput under load, roughly 70 MB resident) is the baseline to beat. **The native
budget should be set after the first measurement, not guessed**: record it in `check_budget.py` as a separate
profile and require native to be no worse than the adapter on every metric.

## Rollout phases

Aligned with the five-step PR sequence in [upstream-integration.md](../upstream-integration.md). Each phase has
an exit criterion that is a test result, not a judgement.

| Phase | Content | Exit criterion |
|---|---|---|
| 0. Proposal | Human-written RFC (outline: [rfc-outline.md](rfc-outline.md)); spikes S1 to S4 answered on a scratch branch | RFC labelled `rfc:on-roadmap` by the code owners |
| 1. Card and non-streaming JSON-RPC | `a2a_api.{h,cpp}`, route registration, auth allowlist, config key, Agent Card, `SendMessage`, `GetTask`, limits and errors | Black-box suite green for non-streaming rows; Card golden file equal to the adapter's modulo URLs; TCK MUST-level clean for the declared capabilities |
| 2. Streaming and cancellation | SSE, event log, `CancelTask`, deadline, close-before-`FAILED`, error-in-200 handling | Cancellation proof within the adapter's latency; stalled-client test; 24 start-then-cancel races leave no running task |
| 3. HTTP+JSON | REST binding, content-type and error shapes | Same suites on both bindings; interop clients 10 of 10 |
| 4. Evidence | Full TCK, interop, benchmarks, fuzzing | Native at or better than the adapter on the recorded baselines; evidence committed |

Not before phase 0: any C++ in the Lemonade repository.

## Risks

| Risk | Mitigation |
|---|---|
| Streaming entry point writes to an HTTP sink, not a callback (see [state-and-lifecycle.md](state-and-lifecycle.md)) | Spike S1; fall back to a small Router PR adding a delta callback |
| TCK needs deterministic scenario answers | Spike S3 before phase 1; a mock model recipe for CI |
| Single API key means no per-user isolation | Document as a known limit; do not invent a second auth system |
| Upstream scope concerns (an agent protocol is a scope increase in its own policy's words) | The RFC leads with the thin-translation framing and the measured evidence, and offers to start under an existing working group |
| Maintenance burden of a second protocol parser | Keep the module small; the shared black-box suites are the regression net |
