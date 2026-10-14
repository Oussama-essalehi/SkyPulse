import { describe, expect, it } from "vitest";

import { airportCode, fromColumns, searchAirports, visibleAirports } from "./airports";
import type { AirportColumns } from "./types";

const columns: AirportColumns = {
  icao: ["EGLL", "LFLL", "LFPB", "LFPG", "LFPN"],
  iata: ["LHR", "LYS", "LBG", "CDG", null],
  name: ["London Heathrow", "Lyon Saint-Exupéry", "Paris-Le Bourget", "Paris CDG", "Toussus"],
  city: ["London", "Lyon", "Paris", "Paris", "Toussus-le-Noble"],
  country: ["GB", "FR", "FR", "FR", "FR"],
  lat: [51.47, 45.73, 48.97, 49.01, 48.75],
  lon: [-0.46, 5.08, 2.44, 2.55, 2.11],
  kind: ["L", "L", "M", "L", "S"],
};
const airports = fromColumns(columns);

describe("airports", () => {
  it("rebuilds objects from the column-oriented payload", () => {
    expect(airports[3]).toEqual({
      icao: "LFPG",
      iata: "CDG",
      name: "Paris CDG",
      city: "Paris",
      country: "FR",
      lat: 49.01,
      lon: 2.55,
      kind: "L",
    });
    expect(airportCode(airports[4])).toBe("LFPN");
  });

  it("shows large airports first, smaller ones when zooming in", () => {
    expect(visibleAirports(airports, 4).length).toBe(0);
    expect(visibleAirports(airports, 5).map((a) => a.icao)).toEqual(["EGLL", "LFLL", "LFPG"]);
    expect(visibleAirports(airports, 7).length).toBe(4);
    expect(visibleAirports(airports, 9).length).toBe(5);
  });

  it("finds an airport by code, then by city or name", () => {
    expect(searchAirports(airports, "cdg")[0].icao).toBe("LFPG");
    expect(searchAirports(airports, "LFPG")[0].icao).toBe("LFPG");
    // City: the large airport before the business one, accents ignored.
    expect(searchAirports(airports, "paris").map((a) => a.icao)).toEqual(["LFPG", "LFPB"]);
    expect(searchAirports(airports, "exupery")[0].icao).toBe("LFLL");
    expect(searchAirports(airports, "LF").length).toBe(0); // too short
    expect(searchAirports(airports, "LFP").map((a) => a.icao)).toEqual(["LFPG", "LFPB", "LFPN"]);
  });
});
