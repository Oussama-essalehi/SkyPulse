"""Local alternative: the same tools with a model served by Ollama (native /api/chat).

Useful offline or to avoid sending questions to an external API. Smaller local models
follow the SQL instructions less reliably: the guardrails apply all the same.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from skyradar_assistant.agent import TOOL_SPECS, Toolbox, call_tool, system_prompt

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")


def _ollama_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in TOOL_SPECS
    ]


def ask_ollama(
    question: str,
    toolbox: Toolbox,
    schema: dict[str, list[dict[str, str]]],
    base_url: str | None = None,
    max_iterations: int = 6,
) -> dict[str, Any]:
    base_url = (base_url or os.environ["OLLAMA_BASE_URL"]).rstrip("/")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt(schema)},
        {"role": "user", "content": question},
    ]
    answer = ""
    with httpx.Client(timeout=180) as client:
        for _ in range(max_iterations):
            response = client.post(
                f"{base_url}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": messages,
                    "tools": _ollama_tools(),
                    "stream": False,
                },
            )
            response.raise_for_status()
            message = response.json()["message"]
            messages.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                answer = message.get("content", "")
                break
            for call in calls:
                fn = call["function"]
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_name": fn["name"],
                        "content": call_tool(toolbox, fn["name"], args),
                    }
                )
    return {
        "answer": answer.strip() or "Pas de réponse.",
        "sql": toolbox.trace.sql,
        "columns": toolbox.trace.columns,
        "rows": toolbox.trace.rows,
        "tools_used": toolbox.trace.tools_used,
        "model": OLLAMA_MODEL,
        "provider": "ollama",
    }
