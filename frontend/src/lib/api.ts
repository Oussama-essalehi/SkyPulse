import type {
  AircraftDetail,
  AirportBoard,
  AirportColumns,
  Alert,
  AssistantAnswer,
  WeatherResponse,
} from "./types";

/**
 * Base URL of the SkyRadar API. Defaults to the same-origin `/api` prefix
 * (proxied by nginx in Docker and by Vite in dev). Set VITE_API_URL at build
 * time when the API lives elsewhere (e.g. Cloudflare Pages + Fly.io).
 */
export const API_BASE: string = (import.meta.env.VITE_API_URL as string | undefined) || "/api";

export function liveSocketUrl(): string {
  if (API_BASE.startsWith("http")) return `${API_BASE.replace(/^http/, "ws")}/ws/live`;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}${API_BASE}/ws/live`;
}

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      detail = body.detail ?? detail;
    } catch {
      /* not JSON */
    }
    // Rate limits (public demo) carry a readable message: show it as is.
    throw new Error(response.status === 429 ? detail : `${response.status} ${detail}`);
  }
  return (await response.json()) as T;
}

export const api = {
  aircraft: (icao24: string) => getJson<AircraftDetail>(`/aircraft/${icao24}`),
  alerts: () => getJson<Alert[]>("/alerts"),
  airports: () => getJson<AirportColumns>("/airports"),
  airportBoard: (icao: string, limit = 40) =>
    getJson<AirportBoard>(`/airports/${icao}/board?limit=${limit}`),
  airportWeather: (icao: string) => getJson<WeatherResponse>(`/airports/${icao}/weather`),
  ask: (question: string) =>
    getJson<AssistantAnswer>("/assistant/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    }),
};
