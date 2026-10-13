"""Redpanda topic names (kept in one place so services cannot drift)."""

AIRCRAFT_STATES = "aircraft.states"
AIRCRAFT_SNAPSHOTS = "aircraft.snapshots"
AIRCRAFT_ALERTS = "aircraft.alerts"

ALL_TOPICS = (AIRCRAFT_STATES, AIRCRAFT_SNAPSHOTS, AIRCRAFT_ALERTS)
