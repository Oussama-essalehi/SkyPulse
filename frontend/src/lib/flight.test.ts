import { describe, expect, it } from "vitest";

import {
  airportLabel,
  cleanTrack,
  clock,
  duration,
  flightBounds,
  flightPath,
  placeName,
  type TrailPoint,
} from "./flight";
import { haversineKm } from "./geo";
import type { FlightInfo } from "./types";

const base: FlightInfo = {
  departure: null,
  arrival: null,
  departure_source: null,
  arrival_source: null,
  departure_time: null,
  departure_time_estimated: false,
  elapsed_s: null,
  route_km: null,
  flown_km: null,
  remaining_km: null,
  progress: null,
  route_flights: null,
  route_share: null,
  rejected_route: null,
};

describe("flight formatting", () => {
  it("formats durations", () => {
    expect(duration(45 * 60)).toBe("45 min");
    expect(duration(2 * 3600 + 5 * 60)).toBe("2 h 05");
    expect(duration(-10)).toBe("0 min");
  });

  it("gives UTC and the viewer's local time", () => {
    const t = Date.UTC(2026, 9, 14, 9, 5) / 1000;
    expect(clock(t, "Europe/Paris")).toEqual({ utc: "09:05 UTC", local: "11:05" });
    expect(clock(t, "UTC")).toEqual({ utc: "09:05 UTC", local: null });
  });

  it("prefers the IATA code and the city", () => {
    const lhr = {
      icao: "EGLL",
      iata: "LHR",
      name: "London Heathrow Airport",
      city: "London",
      country: "United Kingdom",
      latitude: 51.47,
      longitude: -0.46,
    };
    expect(airportLabel(lhr)).toEqual({ code: "LHR", place: "London" });
    expect(airportLabel({ ...lhr, iata: null, city: null })).toEqual({
      code: "EGLL",
      place: "London Heathrow Airport",
    });
    expect(airportLabel(null)).toEqual({ code: "?", place: "Inconnu" });
  });
});

describe("placeName", () => {
  it("drops the commune detail of OurAirports", () => {
    expect(placeName("Paris (Roissy-en-France, Val-d'Oise)")).toBe("Paris");
    expect(placeName("Lyon")).toBe("Lyon");
    expect(placeName(null)).toBeNull();
  });
});

describe("flightPath", () => {
  const tarbes = {
    icao: "LFBT",
    iata: "LDE",
    name: "Tarbes-Lourdes-Pyrénées",
    city: "Tarbes",
    country: "France",
    latitude: 43.18,
    longitude: 0.0,
  };
  const t0 = 1_800_000_000;
  // First seen over Poitou, flying north towards Paris.
  const trail: TrailPoint[] = [
    [0.3, 46.5, 9000, t0],
    [0.8, 47.5, 9000, t0 + 600],
  ];

  it("links the departure airport to the first observed point when they are apart", () => {
    const path = flightPath(trail, { ...base, departure: tarbes }, [1.2, 48.2]);
    expect(path.observed).toEqual([
      [0.3, 46.5],
      [0.8, 47.5],
      [1.2, 48.2],
    ]);
    const link = path.fromDeparture!;
    expect(link[0]).toEqual([0.0, 43.18]);
    expect(link[link.length - 1][0]).toBeCloseTo(0.3, 6);
    expect(link[link.length - 1][1]).toBeCloseTo(46.5, 6);
    // Points in between stay on the great circle (no detour).
    const direct = haversineKm(0, 43.18, 0.3, 46.5);
    let along = 0;
    for (let i = 1; i < link.length; i++)
      along += haversineKm(link[i - 1][0], link[i - 1][1], link[i][0], link[i][1]);
    expect(along).toBeCloseTo(direct, 0);
  });

  it("drops the previous flight before an observed take-off, no link when it starts there", () => {
    const takeoff = t0 + 300;
    const fromTarbes: TrailPoint[] = [
      [2.0, 48.9, 0, t0], // previous leg, before the take-off
      [0.05, 43.2, 300, takeoff],
      [0.2, 43.6, 4000, takeoff + 300],
    ];
    const path = flightPath(
      fromTarbes,
      { ...base, departure: tarbes, departure_time: takeoff, departure_time_estimated: false },
      null,
    );
    expect(path.observed).toEqual([
      [0.05, 43.2],
      [0.2, 43.6],
    ]);
    expect(path.fromDeparture).toBeNull();
  });

  it("draws the observed track alone without a known departure", () => {
    expect(flightPath(trail, null, null)).toEqual({
      observed: [
        [0.3, 46.5],
        [0.8, 47.5],
      ],
      fromDeparture: null,
      toArrival: null,
    });
  });

  it("plans the rest of the route to the destination", () => {
    const orly = { ...tarbes, icao: "LFPO", iata: "ORY", latitude: 48.72, longitude: 2.38 };
    const path = flightPath(trail, { ...base, departure: tarbes, arrival: orly }, [1.2, 48.2]);
    const rest = path.toArrival!;
    expect(rest[0]).toEqual([1.2, 48.2]);
    expect(rest[rest.length - 1][0]).toBeCloseTo(2.38, 6);
    // Already at the destination: nothing left to draw.
    expect(flightPath(trail, { ...base, arrival: orly }, [2.38, 48.7]).toArrival).toBeNull();
  });

  it("frames the whole flight, except across the antimeridian", () => {
    expect(
      flightBounds([
        [0, 43.18],
        [1.2, 48.2],
        [2.38, 48.72],
      ]),
    ).toEqual([
      [0, 43.18],
      [2.38, 48.72],
    ]);
    expect(
      flightBounds([
        [170, 50],
        [-170, 55],
      ]),
    ).toBeNull();
    expect(flightBounds([])).toBeNull();
  });
});

describe("cleanTrack", () => {
  const t0 = 1_800_000_000;
  it("drops an isolated ADS-B glitch", () => {
    const track: TrailPoint[] = [
      [2.0, 48.0, 9000, t0],
      [105.0, 47.0, 9000, t0 + 60], // "Mongolia" one minute later
      [2.3, 48.1, 9000, t0 + 120],
    ];
    expect(cleanTrack(track).map((p) => p[0])).toEqual([2.0, 2.3]);
  });

  it("keeps only the latest consistent stretch after impossible jumps", () => {
    // The screenshot case: an aircraft sending garbage, thousands of km apart in minutes.
    const garbage: TrailPoint[] = [
      [105.0, 47.0, 0, t0], // Mongolia
      [2.0, 47.0, 0, t0 + 300], // France
      [60.0, -30.0, 0, t0 + 600], // Indian Ocean
      [60.01, -30.01, 0, t0 + 660],
    ];
    expect(cleanTrack(garbage).map((p) => p[0])).toEqual([60.0, 60.01]);
  });

  it("leaves a normal track untouched", () => {
    const track: TrailPoint[] = [
      [2.0, 48.0, 9000, t0],
      [2.5, 47.6, 9000, t0 + 300],
      [3.0, 47.2, 9000, t0 + 600],
    ];
    expect(cleanTrack(track)).toEqual(track);
  });

  it("does not link the departure airport when it could not have been flown from", () => {
    const berlin = {
      icao: "EDDB",
      iata: "BER",
      name: "Berlin Brandenburg",
      city: "Berlin",
      country: "DE",
      latitude: 52.36,
      longitude: 13.5,
    };
    const flight = { ...base, departure: berlin, departure_time: t0 - 4 * 3600 };
    const track: TrailPoint[] = [
      [60.0, -30.0, 0, t0],
      [60.01, -30.01, 0, t0 + 60],
    ];
    // ~9 300 km in 4 h: 650 m/s on average, no airliner does that.
    expect(flightPath(track, flight, [60.01, -30.01]).fromDeparture).toBeNull();
    // Over Poland 1 h after leaving Berlin: fine.
    const poland: TrailPoint[] = [[19.0, 52.0, 10000, t0]];
    const recent = { ...flight, departure_time: t0 - 3600 };
    expect(flightPath(poland, recent, [19.1, 52.0]).fromDeparture).not.toBeNull();
  });
});
