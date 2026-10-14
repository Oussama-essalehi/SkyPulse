"""Local alternative: the same tools with a model served by Ollama (native /api/chat).

Useful offline or to avoid sending questions to an external API. Smaller local models
follow the SQL instructions less reliably: the guardrails apply all the same. They also
answer off-topic questions despite the system prompt, and copy numbers from previous
answers instead of calling the tools again on a follow-up. So a first short call (JSON
output constrained by a schema) rewrites the question to stand alone and classifies it
(``on_topic``); the tool loop then answers the standalone question.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from skyradar_assistant.agent import (
    OFF_TOPIC_ANSWER,
    TOOL_SPECS,
    Message,
    Toolbox,
    call_tool,
    conversation,
    system_prompt,
)

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
# Keep the model in memory between questions (reloading it takes seconds to minutes).
KEEP_ALIVE = "30m"
# Per model call: a 7B model on a laptop CPU needs tens of seconds for the long prompt.
CALL_TIMEOUT_S = 180

TOPIC_PROMPT = """Tu prépares les messages envoyés à l'assistant de SkyRadar, un site qui
affiche le trafic aérien mondial en direct (avions, vols, aéroports, compagnies, alertes).
Réponds en JSON avec deux champs.

question : le message réécrit en question complète et autonome. Si c'est une relance
courte de la question précédente (« et hier ? », « et en Espagne ? »), reprends-en le
contexte. Si le message est déjà complet, recopie-le tel quel.

on_topic = true si la question concerne l'aviation ou le trafic aérien : avions, vols,
aéroports (y compris leur météo), compagnies aériennes, routes, alertes, statistiques de
trafic, fonctionnement de SkyRadar.
on_topic = false pour tout le reste, même une question simple ou amusante : culture
générale, sciences, physique, mathématiques, programmation, histoire, sport, cuisine,
santé, météo sans rapport avec un aéroport, conseils personnels, écriture de textes.

Exemples :
« Combien d'avions au-dessus de l'Italie ? »
-> {"question": "Combien d'avions au-dessus de l'Italie ?", "on_topic": true}
« Quel temps fait-il à l'aéroport de Nice ? »
-> {"question": "Quel temps fait-il à l'aéroport de Nice ?", "on_topic": true}
« Comment un avion tient-il en l'air ? »
-> {"question": "Comment un avion tient-il en l'air ?", "on_topic": true}
Question précédente « Combien d'avions survolent la France en ce moment ? », message
« et en Allemagne ? »
-> {"question": "Combien d'avions survolent l'Allemagne en ce moment ?", "on_topic": true}
Question précédente « Combien de vols à Orly aujourd'hui ? », message « et hier ? »
-> {"question": "Combien de vols à Orly hier ?", "on_topic": true}
Sans question précédente, message « et en Belgique ? »
-> {"question": "Combien d'avions en Belgique en ce moment ?", "on_topic": true}
« Quelle est la formule de l'énergie cinétique ? »
-> {"question": "Quelle est la formule de l'énergie cinétique ?", "on_topic": false}
« Quel temps fera-t-il ce week-end à Marseille ? »
-> {"question": "Quel temps fera-t-il ce week-end à Marseille ?", "on_topic": false}
Question précédente « Combien de vols à Orly ? », message « et la capitale du Pérou ? »
-> {"question": "Quelle est la capitale du Pérou ?", "on_topic": false}"""

# Greetings and thanks need no model call (and must not be declined).
SMALL_TALK = re.compile(
    r"^(bonjour|bonsoir|salut|coucou|hello|hi|hey|merci( beaucoup| bien)?|thanks|ok(ay)?|"
    r"super|parfait|génial|top|d'accord|au revoir|bonne (journée|soirée))\b[\s!.,]*\w*[\s!.]*$",
    re.IGNORECASE,
)


class OllamaError(RuntimeError):
    """Ollama answered with an error (model not pulled, bad request...)."""


def _post_chat(client: httpx.Client, base_url: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        response = client.post(
            f"{base_url}/api/chat", json={"model": OLLAMA_MODEL, "keep_alive": KEEP_ALIVE, **body}
        )
    except httpx.TimeoutException as exc:
        raise OllamaError(
            f"Ollama n'a pas répondu à temps : le modèle « {OLLAMA_MODEL} » est trop lent pour"
            " cette machine ou occupé, réessayez ou prenez un modèle plus léger"
        ) from exc
    except httpx.ConnectError as exc:
        raise OllamaError(
            f"Ollama injoignable à {base_url} : vérifiez qu'il est lancé (« ollama serve »)"
            " et OLLAMA_BASE_URL dans .env"
        ) from exc
    if response.status_code == 404:
        raise OllamaError(
            f"modèle « {OLLAMA_MODEL} » absent d'Ollama : lancez « ollama pull {OLLAMA_MODEL} »"
            " ou corrigez OLLAMA_MODEL dans .env (voir « ollama list »)"
        )
    response.raise_for_status()
    result: dict[str, Any] = response.json()
    return result


def triage(
    client: httpx.Client, base_url: str, question: str, history: list[Message]
) -> tuple[bool, str | None]:
    """Whether the question is about air traffic / SkyRadar, and the question rewritten to
    stand alone (follow-ups like "et en Espagne ?" get the previous question's context).

    Small models answer a standalone question far more reliably than a follow-up in a
    conversation (they copy previous numbers instead of calling the tools again). Fails
    open: an unreadable verdict lets the question through, without a rewrite."""
    if SMALL_TALK.match(question.strip()):
        return True, question
    previous = [m["content"] for m in history if m.get("role") == "user"][-1:]
    context = f"Question précédente : « {previous[0]} »\n" if previous else ""
    result = _post_chat(
        client,
        base_url,
        {
            "messages": [
                {"role": "system", "content": TOPIC_PROMPT},
                {"role": "user", "content": f"{context}Message : « {question} »"},
            ],
            # Rewrite first: the verdict is then given on the complete question.
            "format": {
                "type": "object",
                "properties": {"question": {"type": "string"}, "on_topic": {"type": "boolean"}},
                "required": ["question", "on_topic"],
            },
            "options": {"temperature": 0},
            "stream": False,
        },
    )
    try:
        verdict = json.loads(result["message"]["content"])
        on_topic, rewritten = verdict["on_topic"], verdict["question"]
    except (KeyError, TypeError, ValueError):
        return True, None
    standalone = rewritten.strip() if isinstance(rewritten, str) else ""
    return on_topic is not False, standalone or None


def _off_topic() -> dict[str, Any]:
    return {
        "answer": OFF_TOPIC_ANSWER,
        "sql": None,
        "columns": [],
        "rows": [],
        "tools_used": [],
        "model": OLLAMA_MODEL,
        "provider": "ollama",
        "off_topic": True,
    }


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
    history: list[Message] | None = None,
) -> dict[str, Any]:
    base_url = (base_url or os.environ["OLLAMA_BASE_URL"]).rstrip("/")
    history = history or []
    answer = ""
    with httpx.Client(timeout=CALL_TIMEOUT_S) as client:
        on_topic, standalone = triage(client, base_url, question, history)
        if not on_topic:
            return _off_topic()
        # The rewritten question carries the context: no conversation needed.
        turns = [{"role": "user", "content": standalone}] if standalone else None
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt(schema)},
            *(turns or conversation(history, question)),
        ]
        for _ in range(max_iterations):
            message = _post_chat(
                client,
                base_url,
                {"messages": messages, "tools": _ollama_tools(), "stream": False},
            )["message"]
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
        "question": standalone or question,
    }
