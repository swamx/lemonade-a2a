"""Evidence benchmark: direct Lemonade vs A2A -> Lemonade.

Measures time-to-first-token (TTFT), total latency, streamed chunks/sec and
cancellation, then writes a JSON report and prints a Markdown table. Run a
Lemonade server and lemonade-a2a against the same model. Chunk rate is a proxy
for tokens/sec: servers may batch several tokens per SSE chunk.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
import uuid
from pathlib import Path

import httpx

A2A_HEADERS = {"A2A-Version": "1.0"}

# Reproducible workloads. "short" is the default; the others stress prompt
# processing (long input) and sustained generation (long output).
_PARAGRAPH = (
    "Local inference keeps data on the device, removes network round trips and lets "
    "applications work offline, but it shares limited memory and compute between the "
    "model and everything else running on the machine. "
)
WORKLOADS = {
    "short": "Explain local AI in two sentences.",
    # ~700 tokens: fits models Lemonade loads with a small context (Gemma-4-12B gets 1,459 on 8 GB).
    "medium-prompt": _PARAGRAPH * 20 + "\n\nSummarize the text above in two sentences.",
    "long-prompt": _PARAGRAPH * 45 + "\n\nSummarize the text above in two sentences.",
    "long-output": "Write a detailed, well-structured explanation of about 400 words on how local AI inference works.",
}


def rpc(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}


def user_message(prompt: str) -> dict:
    return {
        "message": {
            "messageId": str(uuid.uuid4()),
            "role": "ROLE_USER",
            "parts": [{"text": prompt}],
        }
    }


async def sse_data(response: httpx.Response):
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data = line[5:].strip()
            if data and data != "[DONE]":
                yield json.loads(data)


def summarize(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "median": statistics.median(values),
        "p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
        "min": ordered[0],
        "max": ordered[-1],
    }


def timing(
    start: float,
    first: float | None,
    chunks: int,
    why: str = "",
    first_any: float | None = None,
    think: int | None = None,
) -> dict:
    end = time.perf_counter()
    if first is None:
        raise RuntimeError(f"stream produced no text chunks{(' (' + why + ')') if why else ''}")
    result = {
        "ttft_ms": (first - start) * 1000,
        "total_ms": (end - start) * 1000,
        "chunks": chunks,
        "chunks_per_s": (chunks - 1) / max(end - first, 1e-9) if chunks > 1 else 0.0,
    }
    if first_any is not None:
        # Time to the first output of ANY kind. For a reasoning model this excludes the random
        # length of its thinking from the comparison, which dominates ``ttft_ms``.
        result["ttft_any_ms"] = (first_any - start) * 1000
        result["think_chunks"] = think or 0
    return result


async def direct_stream(client: httpx.AsyncClient, args: argparse.Namespace) -> dict:
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
        "stream": True,
    }
    start = time.perf_counter()
    first = None
    chunks = reasoning = events = 0
    first_any = None
    finish = error = None
    async with client.stream("POST", args.direct_url, json=payload) as response:
        response.raise_for_status()
        async for event in sse_data(response):
            events += 1
            error = event.get("error") or error
            choice = (event.get("choices") or [{}])[0]
            finish = choice.get("finish_reason") or finish
            delta = choice.get("delta") or {}
            reasoning += bool(delta.get("reasoning_content"))
            if delta.get("reasoning_content") or delta.get("content"):
                first_any = first_any or time.perf_counter()
            if delta.get("content"):
                chunks += 1
                first = first or time.perf_counter()
    why = f"events={events}, reasoning_chunks={reasoning}, finish_reason={finish}, error={error}"
    return timing(start, first, chunks, why, first_any, reasoning)


async def a2a_stream(client: httpx.AsyncClient, args: argparse.Namespace) -> dict:
    start = time.perf_counter()
    first = None
    first_any = None
    chunks = think = 0
    status_note = ""
    async with client.stream(
        "POST",
        args.a2a_url,
        headers=A2A_HEADERS,
        json=rpc("SendStreamingMessage", user_message(args.prompt)),
    ) as response:
        response.raise_for_status()
        async for event in sse_data(response):
            if "error" in event:
                raise RuntimeError(json.dumps(event["error"]))
            status = ((event.get("result") or {}).get("statusUpdate") or {}).get("status") or {}
            if status.get("state") in ("TASK_STATE_FAILED", "TASK_STATE_REJECTED"):
                parts = (status.get("message") or {}).get("parts") or [{}]
                status_note = f"task {status['state']}: {parts[0].get('text', '')}"
            update = (event.get("result") or {}).get("artifactUpdate")
            if update and any(p.get("text") for p in update["artifact"].get("parts", [])):
                if update["artifact"].get("name") == "lemonade-reasoning":
                    think += 1  # only present with LEMONADE_A2A_REASONING=artifact
                else:
                    chunks += 1
                    first = first or time.perf_counter()
                first_any = first_any or time.perf_counter()
    return timing(start, first, chunks, status_note, first_any, think)


async def cancellation_check(client: httpx.AsyncClient, args: argparse.Namespace) -> dict:
    """Start a stream, cancel after the first chunk, then confirm the final state."""
    task_id = None
    async with client.stream(
        "POST",
        args.a2a_url,
        headers=A2A_HEADERS,
        json=rpc("SendStreamingMessage", user_message(args.cancel_prompt)),
    ) as response:
        response.raise_for_status()
        async for event in sse_data(response):
            result = event.get("result") or {}
            task_id = (
                (result.get("task") or {}).get("id")
                or (result.get("statusUpdate") or {}).get("taskId")
                or (result.get("artifactUpdate") or {}).get("taskId")
                or task_id
            )
            if result.get("artifactUpdate") and task_id:
                break
        # Cancel while the stream is still open; closing it first can abort the task.
        start = time.perf_counter()
        reply = await client.post(
            args.a2a_url, headers=A2A_HEADERS, json=rpc("CancelTask", {"id": task_id})
        )
    if not task_id:
        return {"result": "error", "detail": "no task id observed"}
    body = reply.json()
    latency_ms = (time.perf_counter() - start) * 1000
    if "error" in body:
        # Generation may legitimately have finished before the cancel arrived.
        return {"result": "inconclusive", "detail": body["error"].get("message"), "task": task_id}
    state = ((body.get("result") or {}).get("status") or {}).get("state")
    return {
        "result": "pass" if state == "TASK_STATE_CANCELED" else "fail",
        "state": state,
        "cancel_latency_ms": latency_ms,
        "task": task_id,
    }


def adapter_footprint(pid: int | None, idle_seconds: float) -> dict:
    if pid is None:
        return {}
    try:
        import psutil
    except ImportError:
        return {"note": "install psutil to record adapter memory/CPU"}
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return {"note": f"adapter pid {pid} not found"}
    return {
        "rss_mb": proc.memory_info().rss / 1e6,
        "cpu_percent_idle": proc.cpu_percent(interval=idle_seconds),
    }


async def _attempt(call, client: httpx.AsyncClient, args: argparse.Namespace) -> dict | None:
    """One load-test request; ``None`` means it failed or the adapter rejected it."""
    try:
        return await call(client, args)
    except (RuntimeError, httpx.HTTPError):
        return None


async def load_test(args: argparse.Namespace) -> dict:
    """Fire N simultaneous streaming requests, repeated, against direct and A2A."""
    results: dict = {}
    limits = httpx.Limits(max_connections=None, max_keepalive_connections=None)
    async with httpx.AsyncClient(timeout=args.timeout, limits=limits) as client:
        for level in args.concurrency:
            results[str(level)] = {}
            for name, call in (("direct", direct_stream), ("a2a", a2a_stream)):
                outcomes: list[dict | None] = []
                started = time.perf_counter()
                for _ in range(args.load_rounds):
                    outcomes += await asyncio.gather(
                        *(_attempt(call, client, args) for _ in range(level))
                    )
                wall = time.perf_counter() - started
                ok = [o for o in outcomes if o]
                entry = {
                    "requests": len(outcomes),
                    "succeeded": len(ok),
                    "failed_or_rejected": len(outcomes) - len(ok),
                    "wall_s": wall,
                    "requests_per_s": len(ok) / wall,
                }
                if ok:
                    entry["ttft_ms"] = summarize([o["ttft_ms"] for o in ok])
                    entry["total_ms"] = summarize([o["total_ms"] for o in ok])
                results[str(level)][name] = entry
    return results


async def run(args: argparse.Namespace) -> dict:
    direct: list[dict] = []
    a2a: list[dict] = []
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        for _ in range(args.warmup):
            await direct_stream(client, args)
            await a2a_stream(client, args)
        for _ in range(args.runs):
            direct.append(await direct_stream(client, args))
            a2a.append(await a2a_stream(client, args))
        cancel = await cancellation_check(client, args)

    report: dict = {
        "model": args.model,
        "workload": args.workload if args.prompt == WORKLOADS[args.workload] else "custom",
        "prompt_words": len(args.prompt.split()),
        "prompt": args.prompt[:200],
        "runs": args.runs,
        "cancellation": cancel,
        "adapter_footprint": adapter_footprint(args.a2a_pid, args.idle_seconds),
    }
    if args.concurrency:
        report["load"] = await load_test(args)
    metrics = ["ttft_ms", "total_ms", "chunks_per_s"]
    if all("ttft_any_ms" in r for r in direct + a2a):
        metrics += ["ttft_any_ms", "think_chunks"]
    for metric in metrics:
        report[metric] = {
            "direct": summarize([r[metric] for r in direct]),
            "a2a": summarize([r[metric] for r in a2a]),
        }
    return report


def to_markdown(report: dict) -> str:
    rows = [
        ("TTFT (ms)", "ttft_ms"),
        ("Total latency (ms)", "total_ms"),
        ("Chunks/sec", "chunks_per_s"),
    ]
    if "ttft_any_ms" in report:
        rows.insert(1, ("TTFT, first output of any kind (ms)", "ttft_any_ms"))
        rows.append(("Reasoning chunks per run", "think_chunks"))
    lines = [
        f"Model: `{report['model']}` - {report['runs']} runs (median / p95)",
        "",
        "| Metric | Direct Lemonade | A2A -> Lemonade | Delta (median) |",
        "|---|---|---|---|",
    ]
    for label, key in rows:
        d, a = report[key]["direct"], report[key]["a2a"]
        lines.append(
            f"| {label} | {d['median']:.1f} / {d['p95']:.1f} | "
            f"{a['median']:.1f} / {a['p95']:.1f} | {a['median'] - d['median']:+.1f} |"
        )
    lines.append(f"| Cancellation | - | {report['cancellation']['result']} | - |")
    footprint = report["adapter_footprint"]
    if "rss_mb" in footprint:
        lines.append(f"| Adapter RSS (MB) | - | {footprint['rss_mb']:.1f} | - |")
        lines.append(f"| Adapter idle CPU (%) | - | {footprint['cpu_percent_idle']:.1f} | - |")
    for level, targets in report.get("load", {}).items():
        if level == next(iter(report["load"])):
            lines += [
                "",
                "| Concurrent | Target | OK / sent | Req/s | TTFT med / p95 (ms) | Total med / p95 (ms) |",
                "|---|---|---|---|---|---|",
            ]
        for name, e in targets.items():
            ttft, total = e.get("ttft_ms"), e.get("total_ms")
            lines.append(
                f"| {level} | {name} | {e['succeeded']}/{e['requests']} | {e['requests_per_s']:.2f} | "
                + (f"{ttft['median']:.0f} / {ttft['p95']:.0f}" if ttft else "-")
                + " | "
                + (f"{total['median']:.0f} / {total['p95']:.0f}" if total else "-")
                + " |"
            )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--direct-url", default="http://127.0.0.1:13305/v1/chat/completions")
    parser.add_argument("--a2a-url", default="http://127.0.0.1:9100")
    parser.add_argument("--model", default=os.getenv("LEMONADE_MODEL", ""))
    parser.add_argument(
        "--workload",
        choices=sorted(WORKLOADS),
        default="short",
        help="preset prompt (ignored if --prompt is set)",
    )
    parser.add_argument("--prompt", default="")
    parser.add_argument("--cancel-prompt", default="Write a long essay about local AI.")
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--a2a-pid", type=int, help="adapter PID for memory/CPU (needs psutil)")
    parser.add_argument("--idle-seconds", type=float, default=3.0)
    parser.add_argument(
        "--concurrency",
        type=lambda v: [int(x) for x in v.split(",")],
        default=[],
        help="comma-separated concurrency levels for the load test, e.g. 1,2,4,8",
    )
    parser.add_argument("--load-rounds", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("benchmark-report.json"))
    args = parser.parse_args()
    args.prompt = args.prompt or WORKLOADS[args.workload]
    if not args.model:
        parser.error("--model is required unless LEMONADE_MODEL is set")
    if args.runs < 1 or args.warmup < 0:
        parser.error("--runs must be positive and --warmup must be non-negative")
    report = asyncio.run(run(args))
    args.output.write_text(json.dumps(report, indent=2))
    print(to_markdown(report))
    print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
