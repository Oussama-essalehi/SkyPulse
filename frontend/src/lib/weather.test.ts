import { describe, expect, it } from "vitest";

import type { AirportWeather } from "./types";
import { skyText, visibilityText, windText } from "./weather";

const base: AirportWeather = {
  station: "LFPG",
  observed_at: 1_791_999_000,
  temperature_c: 18,
  dewpoint_c: 10,
  humidity_pct: 60,
  wind_direction_deg: 110,
  wind_variable: false,
  wind_kmh: 11,
  gust_kmh: null,
  visibility_km: 9.7,
  visibility_plus: true,
  pressure_hpa: 1028,
  conditions: null,
  sky: "ciel dégagé",
  cloud_base_m: null,
  flight_category: "VFR",
  raw: "METAR LFPG 141730Z 11006KT CAVOK 18/10 Q1028 NOSIG",
};

describe("weather formatting", () => {
  it("formats the wind", () => {
    expect(windText(base)).toBe("11 km/h du 110°");
    expect(windText({ ...base, wind_direction_deg: 90, gust_kmh: 33 })).toBe(
      "11 km/h du 090°, rafales 33 km/h",
    );
    expect(windText({ ...base, wind_variable: true, wind_direction_deg: null })).toBe(
      "11 km/h variable",
    );
    expect(windText({ ...base, wind_kmh: 0 })).toBe("calme");
  });

  it("formats the visibility like a METAR reads", () => {
    expect(visibilityText(base)).toBe("10 km ou plus");
    expect(visibilityText({ ...base, visibility_km: 3.2, visibility_plus: false })).toBe("3,2 km");
  });

  it("gives the cloud base when there is one", () => {
    expect(skyText(base)).toBe("ciel dégagé");
    expect(skyText({ ...base, sky: "nuageux", cloud_base_m: 790 })).toBe("nuageux à 790 m");
  });
});
