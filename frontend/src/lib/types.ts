/** Column-oriented payload pushed by `WS /ws/live` (see docs/websocket-protocol.md). */
export interface Columns {
  icao24: string[];
  callsign: (string | null)[];
  country: string[];
  lon: number[];
  lat: number[];
  alt: (number | null)[];
  velocity: (number | null)[];
  track: (number | null)[];
  vrate: (number | null)[];
  on_ground: number[];
  squawk: (string | null)[];
  time_position: number[];
  category: (number | null)[];
}

export interface FrameHeader {
  server_time: number;
  data_time: number | null;
  source: string | null;
  poll_interval: number | null;
  count: number;
}

export interface SnapshotMessage extends FrameHeader {
  type: "snapshot" | "delta";
  upserts: Columns;
  removed: string[];
}

export type Severity = "info" | "warning" | "critical";

export interface Alert {
  alert_id: string;
  icao24: string;
  callsign: string | null;
  type: string;
  severity: Severity;
  status: "active" | "resolved";
  message: string;
  latitude: number | null;
  longitude: number | null;
  altitude: number | null;
  event_time: number;
  detected_at: number;
  details: Record<string, unknown>;
}

export interface AlertsMessage {
  type: "alerts";
  alerts: Alert[];
}

export type LiveMessage = SnapshotMessage | AlertsMessage;

export interface Aircraft {
  icao24: string;
  callsign: string | null;
  country: string;
  lon: number;
  lat: number;
  alt: number | null;
  velocity: number | null;
  track: number | null;
  vrate: number | null;
  onGround: boolean;
  squawk: string | null;
  timePosition: number;
  category: number | null;
  /** Smooth correction when a fresh position replaces the extrapolated one. */
  errLon: number;
  errLat: number;
  errSince: number;
}

export interface AircraftDetail {
  state: Record<string, unknown> & {
    icao24: string;
    callsign: string | null;
    origin_country: string;
    baro_altitude: number | null;
    geo_altitude: number | null;
    velocity: number | null;
    vertical_rate: number | null;
    true_track: number | null;
    squawk: string | null;
    on_ground: boolean;
    last_contact: number;
  };
  trail: [number, number, number | null, number][];
  alerts: Alert[];
  flight: {
    departure?: string | null;
    arrival?: string | null;
    departure_time?: number | null;
  } | null;
  eta: { seconds: number; method: string; airport?: string } | null;
}

export interface Filters {
  minAlt: number;
  maxAlt: number;
  country: string;
  status: "all" | "airborne" | "ground";
}

export interface AssistantAnswer {
  answer: string;
  sql: string | null;
  columns: string[];
  rows: unknown[][];
  tools_used?: string[];
}
