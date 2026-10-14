import { altitudeColor, palette } from "./colors";
import { blendWeight, deadReckon, haversineKm } from "./geo";
import type { Aircraft, Columns, Filters, SnapshotMessage } from "./types";

/** Binary buffers consumed directly by deck.gl (no per-object accessor calls). */
export interface RenderBuffers {
  length: number;
  ids: string[];
  positions: Float32Array;
  angles: Float32Array;
  colors: Uint8Array;
}

const TELEPORT_KM = 50;
export const MAX_ALTITUDE_FILTER = 15000;

/**
 * Mutable aircraft store, deliberately outside React state: with >10 000
 * aircraft, React reconciliation on every frame would be far too slow.
 */
export class AircraftStore {
  readonly aircraft = new Map<string, Aircraft>();
  /** server clock - client clock, seconds */
  clockSkew = 0;
  dataTime: number | null = null;
  source: string | null = null;
  pollInterval: number | null = null;
  version = 0;

  private buffers: RenderBuffers = {
    length: 0,
    ids: [],
    positions: new Float32Array(0),
    angles: new Float32Array(0),
    colors: new Uint8Array(0),
  };

  now(): number {
    return Date.now() / 1000 + this.clockSkew;
  }

  apply(message: SnapshotMessage): void {
    this.clockSkew = message.server_time - Date.now() / 1000;
    this.dataTime = message.data_time;
    this.source = message.source;
    this.pollInterval = message.poll_interval;
    const now = this.now();
    if (message.type === "snapshot") this.aircraft.clear();
    for (const id of message.removed) this.aircraft.delete(id);
    this.upsert(message.upserts, now);
    this.version++;
  }

  private upsert(cols: Columns, now: number): void {
    for (let i = 0; i < cols.icao24.length; i++) {
      const id = cols.icao24[i];
      const previous = this.aircraft.get(id);
      const next: Aircraft = {
        icao24: id,
        callsign: cols.callsign[i],
        country: cols.country[i],
        lon: cols.lon[i],
        lat: cols.lat[i],
        alt: cols.alt[i],
        velocity: cols.velocity[i],
        track: cols.track[i],
        vrate: cols.vrate[i],
        onGround: cols.on_ground[i] === 1,
        squawk: cols.squawk[i],
        timePosition: cols.time_position[i],
        category: cols.category[i],
        errLon: 0,
        errLat: 0,
        errSince: now,
      };
      if (previous) {
        // Keep the drawn position continuous: start from where the plane is
        // displayed now and glide towards the fresh fix.
        const [shownLon, shownLat] = this.position(previous, now);
        const [freshLon, freshLat] = this.position(next, now);
        if (haversineKm(shownLon, shownLat, freshLon, freshLat) < TELEPORT_KM) {
          next.errLon = shownLon - freshLon;
          next.errLat = shownLat - freshLat;
        }
      }
      this.aircraft.set(id, next);
    }
  }

  /** Interpolated (dead-reckoned + blended) position at time `now`. */
  position(a: Aircraft, now: number): [number, number] {
    const [lon, lat] = a.onGround
      ? [a.lon, a.lat]
      : deadReckon(a.lon, a.lat, a.velocity, a.track, now - a.timePosition);
    const w = blendWeight(now - a.errSince);
    return w > 0 ? [lon + a.errLon * w, lat + a.errLat * w] : [lon, lat];
  }

  matches(a: Aircraft, filters: Filters): boolean {
    if (filters.status === "airborne" && a.onGround) return false;
    if (filters.status === "ground" && !a.onGround) return false;
    if (filters.country && a.country !== filters.country) return false;
    if (!a.onGround) {
      const alt = a.alt ?? 0;
      // The slider maximum means "no upper bound" (a few aircraft report > 15 km).
      if (alt < filters.minAlt || (filters.maxAlt < MAX_ALTITUDE_FILTER && alt > filters.maxAlt))
        return false;
    }
    return true;
  }

  /** Fill the render buffers for the current frame (reuses allocations). */
  frame(filters: Filters, selected: string | null): RenderBuffers {
    const n = this.aircraft.size;
    let buf = this.buffers;
    if (buf.positions.length < n * 2) {
      const cap = Math.ceil(n * 1.25) + 64;
      buf = {
        length: 0,
        ids: [],
        positions: new Float32Array(cap * 2),
        angles: new Float32Array(cap),
        colors: new Uint8Array(cap * 4),
      };
    }
    const now = this.now();
    const ids: string[] = [];
    let k = 0;
    for (const a of this.aircraft.values()) {
      if (!this.matches(a, filters) && a.icao24 !== selected) continue;
      const [lon, lat] = this.position(a, now);
      buf.positions[2 * k] = lon;
      buf.positions[2 * k + 1] = lat;
      // deck.gl IconLayer angles are counter-clockwise, tracks are clockwise.
      buf.angles[k] = -(a.track ?? 0);
      const [r, g, b] =
        a.icao24 === selected ? palette().selected : altitudeColor(a.alt, a.onGround);
      buf.colors[4 * k] = r;
      buf.colors[4 * k + 1] = g;
      buf.colors[4 * k + 2] = b;
      buf.colors[4 * k + 3] = 255;
      ids.push(a.icao24);
      k++;
    }
    this.buffers = buf;
    // Fresh subarray views (no copy) so deck.gl detects the change and re-uploads.
    return {
      length: k,
      ids,
      positions: buf.positions.subarray(0, 2 * k),
      angles: buf.angles.subarray(0, k),
      colors: buf.colors.subarray(0, 4 * k),
    };
  }

  countries(): string[] {
    return [...new Set([...this.aircraft.values()].map((a) => a.country))].sort();
  }

  search(query: string, limit = 8): Aircraft[] {
    const q = query.trim().toUpperCase();
    if (q.length < 2) return [];
    const hits: Aircraft[] = [];
    for (const a of this.aircraft.values()) {
      if (a.callsign?.toUpperCase().startsWith(q) || a.icao24.toUpperCase().startsWith(q)) {
        hits.push(a);
        if (hits.length >= limit) break;
      }
    }
    return hits;
  }
}
