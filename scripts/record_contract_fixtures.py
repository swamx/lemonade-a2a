"""Record backend contract fixtures from a real Lemonade server.

    python scripts/record_contract_fixtures.py --model Bonsai-1.7B-gguf [--reasoning-model Gemma-4-12B-it-GGUF]

Writes tests/contract/fixtures/lemonade/<server version>/<case>.sse (the raw stream) and a
sibling .json with what the adapter is expected to make of it (derived by the adapter's own
parser, so review the files before committing). Supporting a new Lemonade release starts here.

Cases: ``short`` (a normal answer), ``reasoning`` (a reasoning model, when given), and
``context-exceeded`` (a prompt longer than the model's context window, which Lemonade reports as
an error event inside a 200 stream).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lemonade_a2a.executor import NO_ANSWER_TEXT
from lemonade_a2a.lemonade_client import (
    BackendStreamError,
    _delta_parts,
    describe_stream_error,
)

PROMPTS = {
    "short": "Explain local AI in two sentences.",
    "reasoning": "In one short sentence, why is the sky blue?",
}


def stream_raw(root: str, model: str, prompt: str, max_tokens: int | None) -> str:
    body: dict = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": True}
    if max_tokens:
        body["max_tokens"] = max_tokens
    lines: list[str] = []
    with httpx.stream("POST", f"{root}/v1/chat/completions", json=body, timeout=900) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("data:"):
                lines.append(line.rstrip())
    return "\n\n".join(lines) + "\n\n"


def expectations(raw: str) -> dict:
    answer = thinking = ""
    failure = None
    for line in raw.splitlines():
        if not line.startswith("data:") or "[DONE]" in line:
            continue
        try:
            content, reasoning = _delta_parts(json.loads(line[5:]))
        except BackendStreamError as exc:
            failure = describe_stream_error(exc)
            break
        answer += content
        thinking += reasoning
    if failure is None and not answer and thinking:
        failure = NO_ANSWER_TEXT
    result = {
        "answer": answer,
        "reasoning": thinking,
        "usage": None,
        "outcome": "failed" if failure else "completed",
    }
    if failure:
        result["failure"] = failure
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lemonade-url", default="http://127.0.0.1:13305")
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-model", help="a model that streams reasoning_content")
    parser.add_argument(
        "--context-model", help="model for the context-exceeded case (default: --model)"
    )
    args = parser.parse_args()

    root = args.lemonade_url.rstrip("/").removesuffix("/v1")
    version = httpx.get(f"{root}/api/v1/health", timeout=10).json()["version"]
    target = ROOT / "tests" / "contract" / "fixtures" / "lemonade" / version
    target.mkdir(parents=True, exist_ok=True)

    cases = {"short": (args.model, PROMPTS["short"], 80)}
    if args.reasoning_model:
        cases["reasoning"] = (args.reasoning_model, PROMPTS["reasoning"], 400)
    models = {m["id"]: m for m in httpx.get(f"{root}/v1/models", timeout=10).json()["data"]}
    context_model = args.context_model or args.model
    window = models.get(context_model, {}).get("context_length") or 4096
    cases["context-exceeded"] = (context_model, "lorem ipsum dolor sit amet " * (window // 2), None)

    for name, (model, prompt, max_tokens) in cases.items():
        sys.stdout.write(f"recording {name} ({model}) ...\n")
        raw = stream_raw(root, model, prompt, max_tokens)
        (target / f"{name}.sse").write_text(raw, encoding="utf-8", newline="\n")
        meta = expectations(raw)
        meta["recorded"] = f"real Lemonade {version}, model {model}"
        (target / f"{name}.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        sys.stdout.write(
            f"  {meta['outcome']}: answer {len(meta['answer'])} chars, reasoning {len(meta['reasoning'])} chars\n"
        )
    sys.stdout.write(f"wrote {target}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
