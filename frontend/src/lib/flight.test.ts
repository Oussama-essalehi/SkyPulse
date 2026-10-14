import { describe, expect, it } from "vitest";

import { airportLabel, clock, duration, sourceNotes } from "./flight";
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

  it("explains every source", () => {
    const notes = sourceNotes({
      ...base,
      departure_source: "route_db",
      arrival_source: "route_db",
      departure_time_estimated: true,
      rejected_route: null,
    });
    expect(notes).toHaveLength(2);
    expect(notes[0]).toContain("adsbdb");
    expect(notes[1]).toContain("estimée");
    const history = sourceNotes({
      ...base,
      departure_source: "reconstructed",
      arrival_source: "history",
      route_flights: 12,
      route_share: 0.75,
      rejected_route: "EGLL → VOBL",
    });
    expect(history).toEqual([
      "Décollage observé par SkyRadar.",
      "Destination habituelle de ce callsign dans l'historique (12 vols, 75 %).",
      "Route publiée EGLL → VOBL ignorée : incohérente avec la position.",
    ]);
  });
});
