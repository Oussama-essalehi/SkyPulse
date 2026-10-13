import json
from pathlib import Path
from typing import Any

import anthropic
import duckdb
import httpx2
import pytest
from anthropic import DefaultHttpxClient

from skyradar_assistant import main
from skyradar_assistant.agent import Toolbox, ask_claude, system_prompt


def message(content: list[dict[str, Any]], stop_reason: str) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


@pytest.fixture
def marts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    duckdb.sql(
        "copy (select * from (values ('LFPG', 'Paris CDG', 120), ('EGLL', 'Heathrow', 95))"
        " t(airport_icao, airport_name, movements))"
        f" to '{tmp_path}/fct_airport_activity.parquet' (format parquet)"
    )
    monkeypatch.setattr(main, "MARTS_ROOT", tmp_path)
    return tmp_path


def test_tool_runner_loop_with_guarded_sql(marts: Path) -> None:
    requests: list[dict[str, Any]] = []
    sql = (
        "select airport_icao, sum(movements) as mouvements from fct_airport_activity "
        "group by all order by mouvements desc"
    )
    replies = [
        message(
            [{"type": "tool_use", "id": "toolu_1", "name": "run_sql", "input": {"query": sql}}],
            "tool_use",
        ),
        message(
            [{"type": "text", "text": "L'aéroport le plus actif est LFPG (120 mouvements)."}],
            "end_turn",
        ),
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json=replies[len(requests) - 1])

    client = anthropic.Anthropic(
        api_key="test", http_client=DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    )
    result = ask_claude(
        "Quel est l'aéroport le plus actif ?",
        Toolbox(main.run_safe_query),
        main.schema(),
        client=client,
    )

    assert result["answer"].startswith("L'aéroport le plus actif est LFPG")
    assert result["tools_used"] == ["run_sql"]
    assert result["rows"][0] == ["LFPG", 120]
    assert "LIMIT" in result["sql"].upper()
    first = requests[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["fallbacks"] == "default"
    assert first["output_config"] == {"effort": "medium"}
    assert {t["name"] for t in first["tools"]} == {
        "run_sql",
        "get_live_stats",
        "count_live_aircraft",
        "get_active_alerts",
    }
    # The tool result sent back to the model carries the guarded rows.
    tool_result = requests[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert "LFPG" in json.dumps(tool_result["content"])


def test_unsafe_sql_is_returned_to_the_model_as_an_error(marts: Path) -> None:
    toolbox = Toolbox(main.run_safe_query)
    reply = toolbox.run_sql("drop table fct_airport_activity")
    assert reply.startswith("Requête refusée")
    assert toolbox.trace.sql is None
    assert "Erreur SQL" in toolbox.run_sql("select nope from fct_airport_activity")


def test_refusal_is_handled(marts: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=message([], "refusal"))

    client = anthropic.Anthropic(
        api_key="test", http_client=DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    )
    result = ask_claude("?", Toolbox(main.run_safe_query), main.schema(), client=client)
    assert result["answer"] == "Je ne peux pas répondre à cette question."


def test_system_prompt_lists_schema(marts: Path) -> None:
    prompt = system_prompt(main.schema())
    assert "fct_airport_activity" in prompt
    assert "movements" in prompt
    assert "français" in prompt


def test_ollama_loop(marts: Path) -> None:
    import httpx
    import respx

    from skyradar_assistant.ollama import ask_ollama

    calls = [
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "run_sql",
                            "arguments": {"query": "select airport_icao from fct_airport_activity"},
                        }
                    }
                ],
            }
        },
        {"message": {"role": "assistant", "content": "Deux aéroports : LFPG et EGLL."}},
    ]
    with respx.mock:
        route = respx.post("http://ollama:11434/api/chat").mock(
            side_effect=[httpx.Response(200, json=c) for c in calls]
        )
        result = ask_ollama(
            "Quels aéroports ?",
            Toolbox(main.run_safe_query),
            main.schema(),
            base_url="http://ollama:11434",
        )
    assert route.call_count == 2
    assert result["provider"] == "ollama"
    assert result["answer"] == "Deux aéroports : LFPG et EGLL."
    assert sorted(r[0] for r in result["rows"]) == ["EGLL", "LFPG"]
