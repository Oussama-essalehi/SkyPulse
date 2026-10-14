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

/** `n` points (ends included) along the great circle between two positions. */
export function greatCircle(
  from: [number, number],
  to: [number, number],
  n: number,
): [number, number][] {
  const [l1, p1] = [from[0] * DEG, from[1] * DEG];
  const [l2, p2] = [to[0] * DEG, to[1] * DEG];
  const d =
    2 *
    Math.asin(
      Math.sqrt(
        Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin((l2 - l1) / 2) ** 2,
      ),
    );
  if (d < 1e-9 || n < 2) return [from, to];
  const points: [number, number][] = [];
  for (let i = 0; i < n; i++) {
    const f = i / (n - 1);
    const a = Math.sin((1 - f) * d) / Math.sin(d);
    const b = Math.sin(f * d) / Math.sin(d);
    const x = a * Math.cos(p1) * Math.cos(l1) + b * Math.cos(p2) * Math.cos(l2);
    const y = a * Math.cos(p1) * Math.sin(l1) + b * Math.cos(p2) * Math.sin(l2);
    const z = a * Math.sin(p1) + b * Math.sin(p2);
    points.push([Math.atan2(y, x) / DEG, Math.atan2(z, Math.sqrt(x * x + y * y)) / DEG]);
  }
  return points;
}
