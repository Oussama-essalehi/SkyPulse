import { useEffect, useState } from "react";

import { api } from "./api";
import type { AirportWeather, WeatherResponse } from "./types";

/** METAR are issued every 30 or 60 min: refreshing every 10 min is plenty. */
const WEATHER_REFRESH_MS = 10 * 60_000;

/** Latest weather of an airport (null icao: nothing fetched). */
export function useWeather(icao: string | null): {
  data: WeatherResponse | null;
  error: string | null;
} {
  const [data, setData] = useState<WeatherResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!icao) return;
    let cancelled = false;
    const load = () =>
      api
        .airportWeather(icao)
        .then((w) => {
          if (!cancelled) {
            setData(w);
            setError(null);
          }
        })
        .catch((e: Error) => !cancelled && setError(e.message));
    void load();
    const timer = window.setInterval(load, WEATHER_REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      setData(null);
      setError(null);
    };
  }, [icao]);
  return { data, error };
}

const fr = (n: number) => n.toLocaleString("fr-FR");

export function windText(w: AirportWeather): string {
  if (w.wind_kmh === null) return "—";
  if (w.wind_kmh === 0) return "calme";
  const from = w.wind_variable
    ? "variable"
    : w.wind_direction_deg !== null
      ? `du ${String(w.wind_direction_deg).padStart(3, "0")}°`
      : "";
  const gust = w.gust_kmh ? `, rafales ${fr(w.gust_kmh)} km/h` : "";
  return `${fr(w.wind_kmh)} km/h ${from}${gust}`.trim();
}

export function visibilityText(w: AirportWeather): string {
  if (w.visibility_km === null) return "—";
  // "6+" statute miles is the METAR's "10 km or more" (9999).
  if (w.visibility_plus && w.visibility_km >= 9.6) return "10 km ou plus";
  return `${w.visibility_plus ? "≥ " : ""}${fr(w.visibility_km)} km`;
}

export function skyText(w: AirportWeather): string {
  if (!w.sky) return "—";
  return w.cloud_base_m !== null ? `${w.sky} à ${fr(w.cloud_base_m)} m` : w.sky;
}

/** Flight category: what the weather allows (visual or instrument flight rules). */
export const CATEGORY: Record<string, { label: string; title: string }> = {
  VFR: { label: "VFR", title: "Conditions de vol à vue" },
  MVFR: { label: "MVFR", title: "Vol à vue marginal (plafond ou visibilité réduits)" },
  IFR: { label: "IFR", title: "Vol aux instruments (plafond bas ou mauvaise visibilité)" },
  LIFR: { label: "LIFR", title: "Vol aux instruments, conditions très dégradées" },
};
