import pytest

from skyradar_assistant.guardrails import UnsafeQueryError, validate_sql

ALLOWED = ["fct_flights", "fct_routes", "fct_traffic_hourly"]


def test_select_gets_a_limit() -> None:
    sql = validate_sql("select callsign from fct_flights", ALLOWED, max_limit=100)
    assert sql.upper().endswith("LIMIT 100")


def test_existing_small_limit_is_kept_and_large_one_capped() -> None:
    assert validate_sql("select * from fct_routes limit 5", ALLOWED).endswith("LIMIT 5")
    assert validate_sql("select * from fct_routes limit 100000", ALLOWED, max_limit=50).endswith(
        "LIMIT 50"
    )


def test_cte_and_union_allowed() -> None:
    sql = validate_sql(
        "with t as (select route from fct_routes) select * from t "
        "union all select route from fct_routes",
        ALLOWED,
    )
    assert "LIMIT" in sql.upper()


@pytest.mark.parametrize(
    "sql",
    [
        "drop table fct_flights",
        "delete from fct_flights",
        "insert into fct_flights values (1)",
        "update fct_flights set callsign = 'x'",
        "create table x as select 1",
        "copy fct_flights to '/tmp/x.csv'",
        "attach '/data/warehouse/skyradar.duckdb' as w",
        "pragma database_list",
        "set enable_external_access = true",
        "select 1; drop table fct_flights",
        "select * from read_parquet('/etc/passwd')",
        "select * from '/data/warehouse/secret.parquet'",
        "select * from read_csv_auto('/etc/passwd')",
        "select * from glob('/data/*')",
        "select * from secret_table",
        "select * from main.fct_flights",
        "select * from fct_flights where icao24 in (select icao24 from duckdb_settings())",
    ],
)
def test_unsafe_queries_rejected(sql: str) -> None:
    with pytest.raises(UnsafeQueryError):
        validate_sql(sql, ALLOWED)


def test_read_only_execution(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import duckdb

    from skyradar_assistant import main

    duckdb.sql(
        "copy (select 'AFR1' as callsign, 42 as n) "
        f"to '{tmp_path}/fct_flights.parquet' (format parquet)"
    )
    monkeypatch.setattr(main, "MARTS_ROOT", tmp_path)
    _sql, columns, rows = main.run_safe_query("select callsign, n from fct_flights")
    assert columns == ["callsign", "n"]
    assert rows == [["AFR1", 42]]
    con = main.read_only_connection()
    with pytest.raises(duckdb.Error):
        con.execute("select * from read_csv_auto('/etc/hostname')").fetchall()
