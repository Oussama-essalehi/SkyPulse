"""Explicit, stable Arrow schemas for the Parquet lake (requires the ``storage`` extra).

The column order and types are part of the lake contract read by dbt: add new
columns at the end, never change the type of an existing one.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pyarrow as pa

from skyradar_common.models import Alert, StateVector

STATES_SCHEMA = pa.schema(
    [
        pa.field("icao24", pa.string(), nullable=False),
        pa.field("callsign", pa.string()),
        pa.field("origin_country", pa.string()),
        pa.field("time_position", pa.int64()),
        pa.field("last_contact", pa.int64(), nullable=False),
        pa.field("longitude", pa.float64(), nullable=False),
        pa.field("latitude", pa.float64(), nullable=False),
        pa.field("baro_altitude", pa.float64()),
        pa.field("on_ground", pa.bool_(), nullable=False),
        pa.field("velocity", pa.float64()),
        pa.field("true_track", pa.float64()),
        pa.field("vertical_rate", pa.float64()),
        pa.field("geo_altitude", pa.float64()),
        pa.field("squawk", pa.string()),
        pa.field("spi", pa.bool_()),
        pa.field("position_source", pa.int32()),
        pa.field("category", pa.int32()),
        pa.field("snapshot_time", pa.int64(), nullable=False),
        pa.field("ingested_at", pa.float64(), nullable=False),
        pa.field("source", pa.string()),
        pa.field("schema_version", pa.int32()),
    ]
)

ALERTS_SCHEMA = pa.schema(
    [
        pa.field("alert_id", pa.string(), nullable=False),
        pa.field("icao24", pa.string(), nullable=False),
        pa.field("callsign", pa.string()),
        pa.field("type", pa.string(), nullable=False),
        pa.field("severity", pa.string(), nullable=False),
        pa.field("status", pa.string(), nullable=False),
        pa.field("message", pa.string()),
        pa.field("latitude", pa.float64()),
        pa.field("longitude", pa.float64()),
        pa.field("altitude", pa.float64()),
        pa.field("event_time", pa.int64(), nullable=False),
        pa.field("detected_at", pa.float64(), nullable=False),
        pa.field("details_json", pa.string()),
        pa.field("schema_version", pa.int32()),
    ]
)


def states_to_table(states: Iterable[StateVector]) -> pa.Table:
    rows = [s.model_dump() for s in states]
    return pa.Table.from_pylist(rows, schema=STATES_SCHEMA)


def alerts_to_table(alerts: Iterable[Alert]) -> pa.Table:
    import orjson

    rows: list[dict[str, Any]] = []
    for alert in alerts:
        row = alert.model_dump(mode="json")
        row["details_json"] = orjson.dumps(row.pop("details")).decode()
        rows.append(row)
    return pa.Table.from_pylist(rows, schema=ALERTS_SCHEMA)


def table_to_states(table: pa.Table) -> list[StateVector]:
    columns = [c for c in STATES_SCHEMA.names if c in table.column_names]
    return [StateVector.model_validate(r) for r in table.select(columns).to_pylist()]
