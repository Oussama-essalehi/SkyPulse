import { greatCircle, haversineKm } from "./geo";
import type { AirportInfo, FlightInfo } from "./types";

/** API trail point: lon, lat, altitude (m), position time (epoch s). */
export type TrailPoint = [number, number, number | null, number];

/** Gap above which the departure airport is linked to the start of the observed track. */
const LINK_MIN_KM = 20;

export function duration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds / 60));
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h > 0 ? `${h} h ${String(m).padStart(2, "0")}` : `${m} min`;
}

/** "14:05 UTC" and the viewer's local time ("16:05", null when the viewer is on UTC). */
export function clock(epoch: number, timeZone?: string): { utc: string; local: string | null } {
  const date = new Date(epoch * 1000);
  const utc = date.toISOString().slice(11, 16);
  const local = date.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone });
  return { utc: `${utc} UTC`, local: local === utc ? null : local };
}

/** "Paris (Roissy-en-France, Val-d'Oise)" -> "Paris": OurAirports details the commune. */
export function placeName(city: string | null | undefined): string | null {
  return city ? city.replace(/\s*\(.*\)\s*$/, "") || city : null;
}

/** Short code (IATA when known, as on boarding passes) and the place name. */
export function airportLabel(airport: AirportInfo | null): { code: string; place: string } {
  if (!airport) return { code: "?", place: "Inconnu" };
  const place = placeName(airport.city) ?? airport.name ?? airport.icao;
  return { code: airport.iata ?? airport.icao, place };
}

/**
 * What to draw for the selected aircraft:
 * - `observed`: its positions as received, from the observed take-off when there is one
 *   (earlier points belong to the previous flight of the same aircraft);
 * - `fromDeparture`: when the track starts away from the departure airport (aircraft first
 *   seen in flight, or SkyRadar started after the take-off), the great circle from the
 *   airport to the first observed point, drawn dashed: estimated, not observed.
 */
export function flightPath(
  trail: TrailPoint[] | null,
  flight: FlightInfo | null | undefined,
  current: [number, number] | null,
): { observed: [number, number][]; fromDeparture: [number, number][] | null } {
  let points = trail ?? [];
  const takeoff = flight?.departure_time_estimated ? null : flight?.departure_time;
  if (takeoff) points = points.filter((p) => p[3] >= takeoff - 120);
  const observed = points.map((p): [number, number] => [p[0], p[1]]);
  if (current) observed.push(current);
  const start = observed[0];
  const dep = flight?.departure;
  if (!start || !dep || dep.latitude === null || dep.longitude === null) {
    return { observed, fromDeparture: null };
  }
  const origin: [number, number] = [dep.longitude, dep.latitude];
  const km = haversineKm(origin[0], origin[1], start[0], start[1]);
  if (km < LINK_MIN_KM) return { observed, fromDeparture: null };
  // Dense enough for the dashes (one every ~15 km) and to follow the curvature.
  const n = Math.min(200, Math.max(4, Math.ceil(km / 15)));
  return { observed, fromDeparture: greatCircle(origin, start, n) };
}
