const EARTH_RADIUS_M = 6_371_000;
const DEG = Math.PI / 180;

/** Maximum dead-reckoning horizon: beyond this the aircraft is drawn at its last fix. */
export const MAX_EXTRAPOLATION_S = 20 * 60;
/** Duration of the visual blend between the extrapolated and the fresh position. */
export const BLEND_S = 3;

/**
 * Dead reckoning on the sphere: position after flying `elapsed` seconds at
 * `velocity` m/s on a constant `track` (degrees clockwise from north).
 */
export function deadReckon(
  lon: number,
  lat: number,
  velocity: number | null,
  track: number | null,
  elapsed: number,
): [number, number] {
  if (!velocity || track === null || elapsed <= 0) return [lon, lat];
  const t = Math.min(elapsed, MAX_EXTRAPOLATION_S);
  const d = (velocity * t) / EARTH_RADIUS_M;
  const b = track * DEG;
  const p1 = lat * DEG;
  const l1 = lon * DEG;
  const sinP2 = Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b);
  const p2 = Math.asin(Math.max(-1, Math.min(1, sinP2)));
  const l2 =
    l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * sinP2);
  return [((((l2 / DEG + 540) % 360) + 360) % 360) - 180, p2 / DEG];
}

/** Fraction of the correction offset still applied `since` seconds after an update. */
export function blendWeight(since: number): number {
  if (since <= 0) return 1;
  if (since >= BLEND_S) return 0;
  // ease-out cubic
  const x = since / BLEND_S;
  return Math.pow(1 - x, 3);
}

export function haversineKm(lon1: number, lat1: number, lon2: number, lat2: number): number {
  const p1 = lat1 * DEG;
  const p2 = lat2 * DEG;
  const dp = p2 - p1;
  const dl = (lon2 - lon1) * DEG;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return (2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(a)))) / 1000;
}
