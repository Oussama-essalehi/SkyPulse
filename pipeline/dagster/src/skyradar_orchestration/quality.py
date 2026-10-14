"""Soda Core data contracts executed as Dagster asset checks.

Each contract in ``data_quality/contracts`` targets one dataset. Every named
check of the contract becomes a Dagster asset check, so failures are visible
check by check in the Dagster UI (with Soda's diagnostics as metadata).

Datasets are exposed to Soda through an **in-memory** DuckDB connection with
views over Parquet files: the dbt database file is never opened (ADR 0003).
"""

# No `from __future__ import annotations`: Dagster inspects runtime type hints.
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import yaml
from dagster import (
    AssetCheckExecutionContext,
    AssetCheckResult,
    AssetCheckSeverity,
    AssetCheckSpec,
    AssetKey,
    AssetsDefinition,
    MetadataValue,
    multi_asset_check,
)

CONTRACTS_DIR = Path(
    os.getenv("DATA_QUALITY_DIR", Path(__file__).parents[4] / "data_quality" / "contracts")
)
LAKE_DAYS = int(os.getenv("DQ_LAKE_DAYS", "2"))
# Operational checks warn (the source can legitimately be slow, e.g. anonymous
# OpenSky polling); correctness checks fail as errors.
WARN_ONLY = {"lake_freshness", "hourly_volume", "lake_not_empty"}


def lake_root() -> str:
    return os.getenv("SKYRADAR_LAKE_ROOT", "s3://skyradar-lake")


def marts_root() -> str:
    return os.getenv("SKYRADAR_MARTS_ROOT", "/data/marts")


def lake_connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    if lake_root().startswith("s3://"):
        con.execute(
            """
            create secret lake (type s3, key_id $key, secret $secret, endpoint $endpoint,
                                region $region, url_style 'path', use_ssl $ssl)
            """,
            {
                "key": os.getenv("S3_ACCESS_KEY", "skyradar"),
                "secret": os.getenv("S3_SECRET_KEY", "skyradar-secret"),
                "endpoint": os.getenv("S3_ENDPOINT_HOST", "localhost:8333"),
                "region": os.getenv("S3_REGION", "us-east-1"),
                "ssl": os.getenv("S3_USE_SSL", "false").lower() == "true",
            },
        )
    return con


def create_raw_states_view(con: duckdb.DuckDBPyConnection) -> int:
    """View over the last ``LAKE_DAYS`` date partitions; returns the number of files."""
    pattern = f"{lake_root()}/raw/aircraft_states/*/*/*.parquet"
    files = [r[0] for r in con.execute("select file from glob(?)", [pattern]).fetchall()]
    if not files:
        return 0
    dates = sorted({f.split("date=")[1].split("/")[0] for f in files})[-LAKE_DAYS:]
    recent = [f for f in files if f.split("date=")[1].split("/")[0] in dates]
    # Views cannot take bound parameters: the (glob-returned) paths are inlined, escaped.
    con.execute(
        f"""
        create or replace view raw_aircraft_states as
        select
            *,
            coalesce(time_position, last_contact) as position_epoch,
            make_timestamp(snapshot_time * 1000000) as snapshot_ts,
            make_timestamp(cast(epoch("date") as bigint) * 1000000)
                + to_hours(cast("hour" as integer)) as partition_hour
        from read_parquet({_sql_list(recent)}, hive_partitioning = true, union_by_name = true)
        """
    )
    return len(recent)


def _sql_list(values: list[str]) -> str:
    return "[" + ", ".join("'" + v.replace("'", "''") + "'" for v in values) + "]"


def create_mart_view(con: duckdb.DuckDBPyConnection, mart: str) -> int:
    path = Path(marts_root()) / f"{mart}.parquet"
    if not path.exists():
        return 0
    con.execute(f"create or replace view {mart} as select * from read_parquet('{path}')")
    return 1


@dataclass(frozen=True)
class Contract:
    file: Path
    asset: AssetKey
    table: str

    @property
    def check_names(self) -> list[str]:
        spec = yaml.safe_load(self.file.read_text())
        names: list[str] = []
        for column in spec.get("columns", []):
            for check in column.get("checks", []):
                names.extend(body["name"] for body in check.values())
        for check in spec.get("checks", []):
            names.extend(body["name"] for body in check.values())
        return names

    def prepare(self, con: duckdb.DuckDBPyConnection) -> int:
        if self.table == "raw_aircraft_states":
            return create_raw_states_view(con)
        return create_mart_view(con, self.table)


CONTRACTS = [
    Contract(
        CONTRACTS_DIR / "raw_aircraft_states.yml",
        AssetKey(["lake", "aircraft_states"]),
        "raw_aircraft_states",
    ),
    Contract(CONTRACTS_DIR / "fct_flights.yml", AssetKey(["fct_flights"]), "fct_flights"),
]


def run_contract(
    contract: Contract, con: duckdb.DuckDBPyConnection, variables: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Verify one contract; returns one dict per check (name, passed, diagnostics)."""
    from soda_core.contracts.api.verify_api import verify_contract_locally
    from soda_duckdb.common.data_sources.duckdb_data_source import DuckDBDataSourceImpl

    source = DuckDBDataSourceImpl.from_existing_cursor(con, name="skyradar")
    session = verify_contract_locally(
        data_sources=[source], contract_file_path=str(contract.file), variables=variables
    )
    results: list[dict[str, Any]] = []
    for verification in session.contract_verification_results:
        for check_result in verification.check_results:
            outcome = str(getattr(check_result.outcome, "name", check_result.outcome))
            diagnostics = getattr(check_result, "diagnostic_metric_values", None) or {}
            results.append(
                {
                    "name": check_result.check.name,
                    "passed": outcome == "PASSED",
                    "outcome": outcome,
                    "diagnostics": {k: v for k, v in diagnostics.items() if v is not None},
                }
            )
    if session.has_errors and not results:
        raise RuntimeError(session.get_errors_str())
    return results


def _results(contract: Contract) -> Iterator[AssetCheckResult]:
    con = lake_connection()
    available = contract.prepare(con)
    names = contract.check_names
    if not available:
        for name in names:
            yield AssetCheckResult(
                asset_key=contract.asset,
                check_name=name,
                passed=False,
                severity=AssetCheckSeverity.WARN,
                metadata={"reason": f"no data yet for {contract.table}"},
            )
        return
    variables = {
        "MIN_ROWS_PER_HOUR": os.getenv("DQ_MIN_ROWS_PER_HOUR", "1000"),
    }
    by_name = {r["name"]: r for r in run_contract(contract, con, variables)}
    for name in names:
        result = by_name.get(name)
        if result is None:
            yield AssetCheckResult(
                asset_key=contract.asset,
                check_name=name,
                passed=False,
                metadata={"reason": "check did not run (see Soda logs)"},
            )
            continue
        metadata: dict[str, Any] = {
            k: MetadataValue.float(float(v)) if isinstance(v, int | float) else str(v)
            for k, v in result["diagnostics"].items()
        }
        metadata["soda_outcome"] = result["outcome"]
        yield AssetCheckResult(
            asset_key=contract.asset,
            check_name=name,
            passed=result["passed"],
            severity=AssetCheckSeverity.WARN if name in WARN_ONLY else AssetCheckSeverity.ERROR,
            metadata=metadata,
        )


def build_soda_checks() -> list[AssetsDefinition]:
    definitions = []
    for contract in CONTRACTS:

        def _make(c: Contract) -> AssetsDefinition:
            @multi_asset_check(
                name=f"soda_{c.table}",
                specs=[
                    AssetCheckSpec(name, asset=c.asset, description=f"Soda: {name}")
                    for name in c.check_names
                ],
                description=f"Soda Core contract {c.file.name}",
            )
            def _checks(context: AssetCheckExecutionContext) -> Iterator[AssetCheckResult]:
                yield from _results(c)

            return _checks

        definitions.append(_make(contract))
    return definitions
