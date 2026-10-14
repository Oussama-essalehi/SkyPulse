import importlib.util
import json
from pathlib import Path

import duckdb
import pytest

from skyradar_assistant import main

EVAL = Path(__file__).parents[1] / "eval" / "run_eval.py"
spec = importlib.util.spec_from_file_location("run_eval", EVAL)
assert spec is not None
assert spec.loader is not None
run_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_eval)


@pytest.fixture
def marts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    duckdb.sql(f"copy (select 'AFR1' callsign, 42 n) to '{tmp_path}/fct_flights.parquet'")
    monkeypatch.setattr(main, "MARTS_ROOT", tmp_path)
    return tmp_path


async def test_mcp_server_lists_and_calls_tools(marts: Path) -> None:
    from skyradar_assistant.mcp_server import server

    names = {t.name for t in await server.list_tools()}
    assert names == {
        "live_stats",
        "count_live_aircraft",
        "active_alerts",
        "describe_marts",
        "run_sql",
    }
    result = await server.call_tool("run_sql", {"query": "select callsign, n from fct_flights"})
    assert "AFR1" in json.dumps(result, default=str)
    refused = await server.call_tool("run_sql", {"query": "drop table fct_flights"})
    assert "refusée" in json.dumps(refused, default=str, ensure_ascii=False)


@pytest.mark.parametrize(
    ("question", "expected", "result", "ok"),
    [
        ({"kind": "scalar"}, [406], {"rows": [[406]], "answer": ""}, True),
        ({"kind": "scalar"}, [1234], {"rows": [], "answer": "Il y a 1 234 vols."}, True),
        ({"kind": "scalar"}, [406], {"rows": [[400]], "answer": "400"}, False),
        (
            {"kind": "scalar", "percent": True, "tol": 0.02},
            [0.1355],
            {"rows": [], "answer": "13,5 % des vols"},
            True,
        ),
        ({"kind": "top"}, ["BAW286", "AFR1734"], {"rows": [["AFR1734", 9]], "answer": ""}, True),
        (
            {"kind": "set"},
            ["SKR100", "LFMN"],
            {"rows": [], "answer": "SKR100 a atterri à LFMN"},
            True,
        ),
        ({"kind": "set"}, ["SKR100", "LFMN"], {"rows": [], "answer": "SKR100"}, False),
        (
            {"kind": "live", "tool": "get_active_alerts"},
            [],
            {"tools_used": ["get_active_alerts"]},
            True,
        ),
    ],
)
def test_grader(question: dict, expected: list, result: dict, ok: bool) -> None:  # type: ignore[type-arg]
    assert run_eval.grade(question, expected, result) is ok


def test_eval_run_is_summarized_and_tracked(
    marts: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = tmp_path / "questions.yaml"
    questions.write_text(
        "questions:\n"
        "  - {id: a, question: 'Combien ?', kind: scalar, sql: select 42}\n"
        "  - {id: b, question: 'Lequel ?', kind: top, sql: select 'AFR1'}\n"
        "  - {id: c, question: 'Et ça ?', kind: scalar, sql: select 7}\n"
    )
    answers = {
        "Combien ?": {"answer": "Il y a 42 vols.", "rows": [], "tools_used": ["run_sql"]},
        "Lequel ?": {"answer": "C'est AFR1.", "rows": [], "tools_used": ["run_sql"]},
        "Et ça ?": {"answer": "Je ne sais pas.", "rows": [], "tools_used": []},
    }
    tracked: list[tuple[dict, dict, dict]] = []  # type: ignore[type-arg]
    monkeypatch.setattr(run_eval, "QUESTIONS", questions)
    monkeypatch.setattr(run_eval, "ask", lambda q: answers[q])
    monkeypatch.setattr(run_eval, "track", lambda *args: tracked.append(args))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("SKYRADAR_API_URL", "http://127.0.0.1:9")
    out = tmp_path / "last_run.json"
    monkeypatch.setattr("sys.argv", ["run_eval", "--out", str(out)])

    assert run_eval.main_cli() == 0
    meta, metrics, payload = tracked[0]
    assert meta["provider"] == "anthropic"
    assert meta["n_questions"] == 3
    assert len(meta["prompt_sha"]) == 12
    assert metrics["accuracy"] == pytest.approx(2 / 3)
    assert metrics["accuracy_top"] == 1.0
    assert metrics["accuracy_scalar"] == 0.5
    assert json.loads(out.read_text())["metrics"] == payload["metrics"]


def test_eval_tracking_goes_through_mlflow_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    from skyradar_ml import tracking

    calls: list[dict] = []  # type: ignore[type-arg]
    monkeypatch.setattr(tracking, "log_run", lambda *a, **kw: calls.append({"args": a, **kw}))
    run_eval.track({"provider": "ollama", "model": "qwen"}, {"accuracy": 1.0}, {"results": []})
    assert calls[0]["args"] == ("assistant-eval", "ollama:qwen")
    assert calls[0]["metrics"] == {"accuracy": 1.0}
    assert "eval_results.json" in calls[0]["artifacts"]
