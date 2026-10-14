"""Evaluate the assistant on questions.yaml.

    make test-lake dbt-build                     # marts from the deterministic test lake
    SKYRADAR_MARTS_ROOT=pipeline/dbt/target/marts uv run python assistant/eval/run_eval.py
    ... --dry-run   # no LLM: checks every reference query and prints the expected answers

Requires ANTHROPIC_API_KEY (or OLLAMA_BASE_URL) for a real run; each question is one
agent run (a few model calls), so a full run costs a few cents with Claude.
Live questions need the SkyRadar API (SKYRADAR_API_URL), otherwise they are skipped.

With MLFLOW_TRACKING_URI set, every real run is logged to the MLflow experiment
``assistant-eval``: provider, model, effort and a fingerprint of the system prompt and of
the question set as parameters; accuracy (overall and per question kind) and latency as
metrics; the per-question results as an artifact. Prompt or model changes can then be
compared run against run.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

QUESTIONS = Path(__file__).with_name("questions.yaml")


def reference(sql: str) -> list[Any]:
    from skyradar_assistant import main

    con = main.read_only_connection()
    try:
        return [row[0] for row in con.execute(sql).fetchall()]
    finally:
        con.close()


def _numbers(result: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for row in result.get("rows") or []:
        for cell in row:
            if isinstance(cell, int | float) and not isinstance(cell, bool):
                values.append(float(cell))
    text = (result.get("answer") or "").replace(" ", " ").replace("\xa0", " ")
    for match in re.findall(r"\d[\d ]*(?:[.,]\d+)?", text):
        try:
            values.append(float(match.replace(" ", "").replace(",", ".")))
        except ValueError:
            continue
    return values


def _strings(result: dict[str, Any]) -> str:
    cells = [str(c) for row in result.get("rows") or [] for c in row]
    return " ".join([*cells, result.get("answer") or ""]).lower()


def grade(question: dict[str, Any], expected: list[Any], result: dict[str, Any]) -> bool:
    kind = question["kind"]
    if kind == "live":
        return question["tool"] in (result.get("tools_used") or [])
    if kind == "scalar":
        target = float(expected[0] or 0)
        tol = float(question.get("tol", 0.01))
        candidates = [target, target * 100] if question.get("percent") else [target]
        return any(
            math.isclose(v, c, rel_tol=tol, abs_tol=0.5 if c == int(c) else 0.01)
            for v in _numbers(result)
            for c in candidates
        )
    haystack = _strings(result)
    values = [str(v).lower() for v in expected]
    if kind == "top":
        return any(v in haystack for v in values)
    return all(v in haystack for v in values)  # set


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def run_metadata(n_questions: int) -> dict[str, Any]:
    """What defines an eval run: provider, model, effort, prompt and question set."""
    from skyradar_assistant import agent, main

    provider = main.llm_provider()
    if provider == "ollama":
        from skyradar_assistant.ollama import OLLAMA_MODEL

        model = OLLAMA_MODEL
    else:
        model = agent.MODEL
    return {
        "provider": provider or "none",
        "model": model,
        "effort": agent.EFFORT if provider == "anthropic" else "n/a",
        "prompt_sha": _sha(agent.system_prompt(main.schema())),
        "questions_sha": _sha(QUESTIONS.read_text()),
        "n_questions": n_questions,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, float]:
    """Accuracy overall and per question kind, latency."""
    if not results:
        return {"accuracy": 0.0, "graded": 0.0}
    metrics = {
        "accuracy": sum(r["correct"] for r in results) / len(results),
        "graded": float(len(results)),
        "latency_mean_s": sum(r["seconds"] for r in results) / len(results),
        "latency_max_s": max(r["seconds"] for r in results),
    }
    for kind in sorted({r["kind"] for r in results}):
        subset = [r for r in results if r["kind"] == kind]
        metrics[f"accuracy_{kind}"] = sum(r["correct"] for r in subset) / len(subset)
    return metrics


def track(meta: dict[str, Any], metrics: dict[str, float], payload: dict[str, Any]) -> None:
    """Log the run to MLflow (no-op without MLFLOW_TRACKING_URI, never fails the eval)."""
    try:
        from skyradar_ml.tracking import log_run
    except ImportError:  # assistant installed alone
        return
    log_run(
        "assistant-eval",
        f"{meta['provider']}:{meta['model']}",
        params=meta,
        metrics=metrics,
        artifacts={"eval_results.json": payload},
    )


def ask(question: str) -> dict[str, Any]:
    from skyradar_assistant import main

    return main.ask(main.AskRequest(question=question))


def main_cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="only check reference queries")
    parser.add_argument("--only", help="comma-separated question ids")
    parser.add_argument("--out", type=Path, default=Path("assistant/eval/last_run.json"))
    args = parser.parse_args()

    spec = yaml.safe_load(QUESTIONS.read_text())["questions"]
    if args.only:
        wanted = set(args.only.split(","))
        spec = [q for q in spec if q["id"] in wanted]

    api_up = False
    with contextlib.suppress(httpx.HTTPError):
        api_up = (
            httpx.get(
                f"{os.getenv('SKYRADAR_API_URL', 'http://localhost:8000')}/health", timeout=3
            ).status_code
            == 200
        )

    results, correct, graded = [], 0, 0
    for q in spec:
        expected = reference(q["sql"]) if "sql" in q else []
        if q["kind"] != "live" and not expected:
            print(f"✗ {q['id']}: reference query returned no rows", file=sys.stderr)
            return 2
        if args.dry_run:
            print(f"• {q['id']:<28} expected: {expected[:5]}")
            continue
        if q["kind"] == "live" and not api_up:
            print(f"- {q['id']:<28} skipped (API not reachable)")
            continue
        started = time.time()
        try:
            answer = ask(q["question"])
        except Exception as exc:  # a failed run counts as wrong
            answer = {"answer": f"ERROR: {exc}", "rows": [], "tools_used": []}
        ok = grade(q, expected, answer)
        graded += 1
        correct += ok
        results.append(
            {
                "id": q["id"],
                "kind": q["kind"],
                "correct": ok,
                "expected": expected[:10],
                "answer": answer.get("answer"),
                "sql": answer.get("sql"),
                "tools_used": answer.get("tools_used"),
                "seconds": round(time.time() - started, 1),
            }
        )
        print(f"{'✓' if ok else '✗'} {q['id']:<28} {answer.get('answer', '')[:90]}")

    if args.dry_run:
        print(f"{len(spec)} reference queries OK")
        return 0
    accuracy = correct / graded if graded else 0.0
    meta = run_metadata(len(spec))
    metrics = summarize(results)
    payload = {"meta": meta, "metrics": metrics, "results": results}
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    track(meta, metrics, json.loads(json.dumps(payload, default=str)))
    print(f"\nAccuracy: {correct}/{graded} = {accuracy:.0%}  (details: {args.out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
