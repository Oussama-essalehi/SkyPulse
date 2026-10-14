from pathlib import Path

import pyarrow.parquet as pq
import pytest

from skyradar_common.models import StateVector
from skyradar_common.parquet import states_to_table

T0 = 1_760_000_400  # 2025-10-09 09:00:00 UTC


def state(icao: str, t: int, lat: float, lon: float, **kw: object) -> StateVector:
    data: dict[str, object] = {
        "icao24": icao,
        "time_position": t,
        "last_contact": t,
        "latitude": lat,
        "longitude": lon,
        "baro_altitude": 10_000.0,
        "velocity": 230.0,
        "snapshot_time": t,
        "ingested_at": float(t),
    }
    data.update(kw)
    return StateVector.model_validate(data)


@pytest.fixture
def lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    states = []
    # 1100 healthy rows in hour 09: 50 aircraft moving ~30 m/s.
    for i in range(1100):
        states.append(state(f"a{i % 50:05x}", T0 + i, 48.0, 2.0 + (i // 50) * 0.02))
    # Teleportation: 1 degree of longitude (~74 km) in 10 s, on 5 aircraft (> 0.1 % of rows).
    for k in range(5):
        states.append(state(f"bad00{k}", T0 + 100, 48.0, 2.0))
        states.append(state(f"bad00{k}", T0 + 110, 48.0, 3.0))
    # Hour 10: too few rows -> hourly volume (only the last hour is excluded from the check).
    states.append(state("c00001", T0 + 3600, 48.0, 2.0))
    # Hour 11: current hour.
    states.append(state("c00001", T0 + 7200, 48.0, 2.0))
    table = states_to_table(states)
    for hour in (9, 10, 11):
        rows = table.filter(
            [((s.snapshot_time - T0) // 3600 + 9) == hour for s in states]  # type: ignore[arg-type]
        )
        out = tmp_path / "raw" / "aircraft_states" / "date=2025-10-09" / f"hour={hour:02d}"
        out.mkdir(parents=True)
        pq.write_table(rows, out / "part-0.parquet")
    monkeypatch.setenv("SKYRADAR_LAKE_ROOT", str(tmp_path))
    return tmp_path


def test_soda_contract_detects_defects(lake: Path) -> None:
    from skyradar_orchestration.quality import CONTRACTS, lake_connection, run_contract

    contract = next(c for c in CONTRACTS if c.table == "raw_aircraft_states")
    con = lake_connection()
    assert contract.prepare(con) == 3
    results = {r["name"]: r for r in run_contract(contract, con, {"MIN_ROWS_PER_HOUR": "1000"})}
    assert set(results) == set(contract.check_names)
    assert results["latitude_in_range"]["passed"]
    assert not results["no_teleportation"]["passed"]
    assert not results["hourly_volume"]["passed"]
    assert results["hourly_volume"]["diagnostics"]["failed_rows_count"] == 1
    assert not results["lake_freshness"]["passed"]  # 2025 data


def test_empty_lake_yields_warnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from skyradar_orchestration.quality import CONTRACTS, _results

    monkeypatch.setenv("SKYRADAR_LAKE_ROOT", str(tmp_path))
    contract = next(c for c in CONTRACTS if c.table == "raw_aircraft_states")
    results = list(_results(contract))
    assert len(results) == len(contract.check_names)
    assert all(not r.passed for r in results)
