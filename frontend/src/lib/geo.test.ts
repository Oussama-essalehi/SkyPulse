import { describe, expect, it } from "vitest";

import { altitudeColor, PALETTE } from "./colors";
import { blendWeight, deadReckon, haversineKm, MAX_EXTRAPOLATION_S } from "./geo";
import { AircraftStore } from "./store";
import { DEFAULT_FILTERS } from "./labels";
import type { SnapshotMessage } from "./types";

describe("deadReckon", () => {
  it("moves east along the equator at the expected distance", () => {
    const [lon, lat] = deadReckon(0, 0, 250, 90, 100); // 25 km
    expect(lat).toBeCloseTo(0, 6);
    expect(haversineKm(0, 0, lon, lat)).toBeCloseTo(25, 1);
  });

  it("does not move without speed or track", () => {
    expect(deadReckon(2, 48, null, 90, 60)).toEqual([2, 48]);
    expect(deadReckon(2, 48, 200, null, 60)).toEqual([2, 48]);
  });

  it("caps the extrapolation horizon", () => {
    const capped = deadReckon(0, 0, 250, 0, MAX_EXTRAPOLATION_S * 10);
    const max = deadReckon(0, 0, 250, 0, MAX_EXTRAPOLATION_S);
    expect(capped).toEqual(max);
  });

  it("wraps around the antimeridian", () => {
    const [lon] = deadReckon(179.9, 0, 250, 90, 200);
    expect(lon).toBeLessThan(-179);
  });
});

describe("blendWeight", () => {
  it("decays from 1 to 0", () => {
    expect(blendWeight(0)).toBe(1);
    expect(blendWeight(1)).toBeGreaterThan(0);
    expect(blendWeight(10)).toBe(0);
  });
});

describe("altitudeColor", () => {
  it("uses the ground color on the ground", () => {
    expect(altitudeColor(0, true)).toEqual(PALETTE.ground);
  });
  it("interpolates between stops", () => {
    const c = altitudeColor(750, false);
    expect(c[0]).toBeGreaterThanOrEqual(251);
  });
});

const message = (
  type: "snapshot" | "delta",
  lon: number,
  removed: string[] = [],
  onGround = false,
): SnapshotMessage => ({
  type,
  server_time: Date.now() / 1000,
  data_time: 1,
  source: "test",
  poll_interval: 10,
  count: 1,
  removed,
  upserts: {
    icao24: ["abc123"],
    callsign: ["TEST1"],
    country: ["France"],
    lon: [lon],
    lat: [48],
    alt: [10000],
    velocity: [0],
    track: [90],
    vrate: [0],
    on_ground: [onGround ? 1 : 0],
    squawk: [null],
    time_position: [Date.now() / 1000],
    category: [null],
  },
});

describe("AircraftStore", () => {
  it("applies snapshots, deltas and removals", () => {
    const store = new AircraftStore();
    store.apply(message("snapshot", 2));
    expect(store.aircraft.size).toBe(1);
    store.apply(message("delta", 2.01));
    const a = store.aircraft.get("abc123")!;
    // The fresh fix is blended from the previously displayed position.
    expect(a.errLon).toBeCloseTo(-0.01, 5);
    store.apply({
      ...message("delta", 2.02),
      upserts: { ...message("delta", 0).upserts, icao24: [] },
      removed: ["abc123"],
    });
    expect(store.aircraft.size).toBe(0);
  });

  it("builds binary render buffers honouring filters", () => {
    const store = new AircraftStore();
    store.apply(message("snapshot", 2));
    const all = store.frame(DEFAULT_FILTERS, null);
    expect(all.length).toBe(1);
    expect(all.positions[0]).toBeCloseTo(2, 4);
    expect(all.angles[0]).toBe(-90);
    const none = store.frame({ ...DEFAULT_FILTERS, country: "Spain" }, null);
    expect(none.length).toBe(0);
  });

  it("hides aircraft on the ground as soon as a minimum altitude is set", () => {
    const store = new AircraftStore();
    store.apply(message("snapshot", 2, [], true));
    const a = store.aircraft.get("abc123")!;
    expect(store.matches(a, DEFAULT_FILTERS)).toBe(true);
    expect(store.matches(a, { ...DEFAULT_FILTERS, minAlt: 500 })).toBe(false);
    expect(store.matches(a, { ...DEFAULT_FILTERS, status: "ground" })).toBe(true);
  });

  it("draws only the selected aircraft in focus mode", () => {
    const store = new AircraftStore();
    store.apply(message("snapshot", 2));
    expect(store.frame(DEFAULT_FILTERS, null, true).length).toBe(1); // nothing selected
    expect(store.frame(DEFAULT_FILTERS, "abc123", true).ids).toEqual(["abc123"]);
    expect(store.frame(DEFAULT_FILTERS, "zzz999", true).length).toBe(0);
  });

  it("keeps only the aircraft of the chosen airport", () => {
    const store = new AircraftStore();
    store.apply(message("snapshot", 2));
    const a = store.aircraft.get("abc123")!;
    const airport = { ...DEFAULT_FILTERS, status: "airport" as const, airport: "LFPG" };
    expect(store.matches(a, airport)).toBe(false); // board not loaded yet
    expect(store.matches(a, { ...airport, aircraft: new Set(["abc123"]) })).toBe(true);
    expect(store.matches(a, { ...airport, aircraft: new Set(["def456"]) })).toBe(false);
  });
});
