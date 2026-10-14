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

/** Airport kind: L = large, M = medium, S = small with scheduled service. */
export type AirportKind = "L" | "M" | "S";

export interface Airport {
  icao: string;
  iata: string | null;
  name: string;
  city: string | null;
  country: string | null;
  lat: number;
  lon: number;
  kind: AirportKind;
}

/** GET /airports: column-oriented, like the live WebSocket payload. */
export interface AirportColumns {
  icao: string[];
  iata: (string | null)[];
  name: string[];
  city: (string | null)[];
  country: (string | null)[];
  lat: number[];
  lon: number[];
  kind: AirportKind[];
}

export type BoardStatus = "approach" | "en_route" | "climb" | "arrived" | "departing" | "parked";

/** One aircraft on an airport board; `airport` is the other end of its flight. */
export interface BoardRow {
  icao24: string;
  callsign: string | null;
  airline: string | null;
  altitude: number | null;
  velocity: number | null;
  vertical_rate: number | null;
  distance_km: number;
  status: BoardStatus;
  airport: AirportInfo | null;
  source?: RouteSource | "geometry" | null;
  eta?: { seconds: number; arrival_estimate: number; method: string } | null;
  departure_time?: number | null;
  departure_time_estimated?: boolean;
}

export interface Movement {
  direction: "departure" | "arrival";
  callsign: string | null;
  airline: string | null;
  typecode: string | null;
  airport_icao: string | null;
  airport_name: string | null;
  time: number;
  duration_min: number | null;
}

export interface AirportBoard {
  airport: AirportInfo & { kind: AirportKind };
  generated_at: number;
  counts: { arrivals: number; departures: number; ground: number };
  arrivals: BoardRow[];
  departures: BoardRow[];
  ground: BoardRow[];
  pending_lookups: number;
  route_lookup: boolean;
  history: {
    departures_24h: number;
    arrivals_24h: number;
    recent: Movement[];
    top_airports: { icao: string; name: string | null; flights: number }[];
  } | null;
}

export interface Filters {
  minAlt: number;
  maxAlt: number;
  country: string;
  /** "airport": only the aircraft of `airport` (arrivals, departures, on the ground). */
  status: "all" | "airborne" | "ground" | "airport";
  airport: string | null;
  /** icao24 of the aircraft of `airport`, from its live board (null while loading). */
  aircraft: ReadonlySet<string> | null;
}

/** Latest METAR of an airport, decoded by the API (GET /airports/{icao}/weather). */
export interface AirportWeather {
  station: string;
  observed_at: number | null;
  temperature_c: number | null;
  dewpoint_c: number | null;
  humidity_pct: number | null;
  wind_direction_deg: number | null;
  wind_variable: boolean;
  wind_kmh: number | null;
  gust_kmh: number | null;
  visibility_km: number | null;
  visibility_plus: boolean;
  pressure_hpa: number | null;
  conditions: string | null;
  sky: string | null;
  cloud_base_m: number | null;
  flight_category: string | null;
  raw: string | null;
}

export interface WeatherResponse {
  airport: string;
  enabled: boolean;
  weather: AirportWeather | null;
}

export interface AssistantAnswer {
  answer: string;
  sql: string | null;
  columns: string[];
  rows: unknown[][];
  tools_used?: string[];
}
