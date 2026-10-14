import type { Airport, AirportColumns, AirportKind } from "./types";

/** Zoom from which each kind of airport is drawn (large hubs first). */
export const AIRPORT_MIN_ZOOM: Record<AirportKind, number> = { L: 4.5, M: 6.5, S: 8.5 };
/** Zoom from which the code (IATA, else ICAO) is written next to the dot. */
export const AIRPORT_LABEL_ZOOM: Record<AirportKind, number> = { L: 5.5, M: 8, S: 9.5 };

export function fromColumns(c: AirportColumns): Airport[] {
  return c.icao.map((icao, i) => ({
    icao,
    iata: c.iata[i],
    name: c.name[i],
    city: c.city[i],
    country: c.country[i],
    lat: c.lat[i],
    lon: c.lon[i],
    kind: c.kind[i],
  }));
}

export function visibleAirports(airports: Airport[], zoom: number): Airport[] {
  return airports.filter((a) => zoom >= AIRPORT_MIN_ZOOM[a.kind]);
}

export function labelledAirports(airports: Airport[], zoom: number): Airport[] {
  return airports.filter((a) => zoom >= AIRPORT_LABEL_ZOOM[a.kind]);
}

export const airportCode = (a: { iata: string | null; icao: string }): string => a.iata ?? a.icao;

const fold = (s: string) =>
  s
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toUpperCase();

const KIND_RANK: Record<AirportKind, number> = { L: 0, M: 1, S: 2 };

/**
 * Airports matching a query: exact IATA / ICAO code first, then codes starting with it,
 * then names and cities containing it (accent-insensitive, from 3 characters).
 */
export function searchAirports(airports: Airport[], query: string, limit = 4): Airport[] {
  const q = fold(query.trim());
  if (q.length < 3) return [];
  const scored: [number, Airport][] = [];
  for (const a of airports) {
    let score: number;
    if (a.iata === q || a.icao === q) score = 0;
    else if (a.icao.startsWith(q)) score = 1;
    else if (fold(a.city ?? "").includes(q) || fold(a.name).includes(q)) score = 2;
    else continue;
    scored.push([score * 10 + KIND_RANK[a.kind], a]);
  }
  return scored
    .sort((x, y) => x[0] - y[0] || x[1].name.localeCompare(y[1].name))
    .slice(0, limit)
    .map(([, a]) => a);
}
