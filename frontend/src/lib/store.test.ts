import { describe, expect, it } from "vitest";

import { DEFAULT_FILTERS } from "./labels";
import { AircraftStore, filtering } from "./store";
import type { Aircraft, Alert, Filters } from "./types";

function aircraft(icao24: string, country: string, alt: number, onGround = false): Aircraft {
  return {
    icao24,
    callsign: null,
    country,
    lon: 2,
    lat: 48,
    alt,
    velocity: null,
    track: null,
    vrate: null,
    onGround,
    squawk: null,
    timePosition: 1000,
    category: null,
    errLon: 0,
    errLat: 0,
    errSince: 0,
  };
}

function alert(icao24: string, severity: Alert["severity"] = "warning"): Alert {
  return {
    alert_id: icao24,
    icao24,
    callsign: null,
    type: "signal_loss",
    severity,
    status: "active",
    message: "",
    latitude: 10,
    longitude: 20,
    altitude: null,
    event_time: 1000,
    detected_at: 1000,
  } as Alert;
}

describe("alert rings follow the filters", () => {
  const store = new AircraftStore();
  store.aircraft.set("fr1", aircraft("fr1", "France", 11000));
  store.aircraft.set("bo1", aircraft("bo1", "Bolivia", 9000));
  // "gone": its signal was lost, the aircraft left the map.
  const alerts = [alert("fr1"), alert("bo1"), alert("gone")];
  const rings = (filters: Filters, selected: string | null = null, focus = false) =>
    store.alertPoints(alerts, filters, selected, focus, 1000).map((p) => p.icao24);

  it("draws every ring without filters, vanished aircraft at their last position", () => {
    expect(rings(DEFAULT_FILTERS)).toEqual(["fr1", "bo1", "gone"]);
    const gone = store.alertPoints(alerts, DEFAULT_FILTERS, null, false, 1000)[2];
    expect(gone.position).toEqual([20, 10]);
  });

  it("keeps only the rings of the aircraft shown", () => {
    expect(rings({ ...DEFAULT_FILTERS, country: "Bolivia" })).toEqual(["bo1"]);
    expect(rings({ ...DEFAULT_FILTERS, minAlt: 10000 })).toEqual(["fr1"]);
    expect(rings({ ...DEFAULT_FILTERS, status: "ground" })).toEqual([]);
  });

  it("keeps the selected aircraft's ring, alone when it has the focus", () => {
    expect(rings({ ...DEFAULT_FILTERS, country: "Bolivia" }, "fr1")).toEqual(["fr1", "bo1"]);
    expect(rings(DEFAULT_FILTERS, "fr1", true)).toEqual(["fr1"]);
  });

  it("knows when a filter is active", () => {
    expect(filtering(DEFAULT_FILTERS)).toBe(false);
    expect(filtering({ ...DEFAULT_FILTERS, status: "airborne" })).toBe(true);
    expect(filtering({ ...DEFAULT_FILTERS, maxAlt: 5000 })).toBe(true);
  });
});
