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
  aircraft?: AircraftInfo | null;
  flight: FlightInfo | null;
  eta: EtaInfo | null;
}

/** OpenSky aircraft database + airline of the callsign (null fields when unknown). */
export interface AircraftInfo {
  registration?: string | null;
  typecode?: string | null;
  manufacturer?: string | null;
  model?: string | null;
  type_name?: string | null;
  wake_category?: string | null;
  operator?: string | null;
  built_year?: number | null;
  airline_icao: string | null;
  airline_name: string | null;
  in_registry: boolean;
}

export interface AirportInfo {
  icao: string;
  iata: string | null;
  name: string | null;
  city: string | null;
  country: string | null;
  latitude: number | null;
  longitude: number | null;
}

/** Where a departure / destination comes from, most trusted first. */
export type RouteSource = "reconstructed" | "route_db" | "history";

export interface FlightInfo {
  departure: AirportInfo | null;
  arrival: AirportInfo | null;
  departure_source: RouteSource | null;
  arrival_source: RouteSource | null;
  departure_time: number | null;
  departure_time_estimated: boolean;
  elapsed_s: number | null;
  route_km: number | null;
  flown_km: number | null;
  remaining_km: number | null;
  progress: number | null;
  route_flights: number | null;
  route_share: number | null;
  rejected_route: string | null;
}

export interface EtaInfo {
  airport: string;
  remaining_km: number;
  seconds: number;
  arrival_estimate: number;
  naive_seconds: number;
  method: "lightgbm" | "naive";
  model_mae_min: number | null;
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
