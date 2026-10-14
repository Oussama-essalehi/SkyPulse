import { altitudeColor, rgbCss } from "../lib/colors";
import type { Aircraft, AircraftDetail } from "../lib/types";
import { ALERT_LABELS } from "../lib/labels";
import { FlightSection } from "./FlightSection";

const WAKE_LABELS: Record<string, string> = {
  L: "avion léger",
  "L/M": "léger à moyen",
  M: "moyen-porteur",
  H: "gros porteur",
  J: "très gros porteur (A380)",
};

interface Props {
  aircraft: Aircraft | undefined;
  detail: AircraftDetail | null;
  error: string | null;
  onClose: () => void;
}

const fmt = (value: number | null | undefined, unit: string, digits = 0) =>
  value === null || value === undefined
    ? "—"
    : `${value.toLocaleString("fr-FR", { maximumFractionDigits: digits })} ${unit}`;

export function AircraftPanel({ aircraft, detail, error, onClose }: Props) {
  if (!aircraft) {
    return (
      <aside className="aircraft-panel">
        <button className="close" onClick={onClose} aria-label="Fermer">
          ×
        </button>
        <p className="muted">Cet avion n'est plus suivi (hors couverture ou atterri).</p>
      </aside>
    );
  }
  const color = rgbCss(altitudeColor(aircraft.alt, aircraft.onGround));
  const flight = detail?.flight;
  const info = detail?.aircraft;
  const airline = info?.airline_name ?? info?.operator ?? info?.airline_icao;
  const type = [info?.model ?? info?.type_name, info?.typecode && `(${info.typecode})`]
    .filter(Boolean)
    .join(" ");
  return (
    <aside className="aircraft-panel">
      <button className="close" onClick={onClose} aria-label="Fermer">
        ×
      </button>
      <header>
        <h2 style={{ color }}>{aircraft.callsign ?? "Sans callsign"}</h2>
        <span className="muted">
          {aircraft.icao24.toUpperCase()} · {aircraft.country}
        </span>
        {info && (airline || type) && (
          <p className="aircraft-info">
            {airline && <strong>{airline}</strong>}
            {airline && type && " · "}
            {type}
            {info.registration && <span className="muted"> · {info.registration}</span>}
          </p>
        )}
        {info && (info.built_year || info.wake_category) && (
          <p className="muted small">
            {[
              info.built_year && `Construit en ${info.built_year}`,
              info.wake_category && WAKE_LABELS[info.wake_category],
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        )}
      </header>
      {detail?.alerts.map((alert) => (
        <div key={alert.alert_id} className={`alert alert-${alert.severity}`}>
          <strong>{ALERT_LABELS[alert.type] ?? alert.type}</strong>
          <span className="alert-message">{alert.message}</span>
        </div>
      ))}
      <FlightSection flight={flight} eta={detail?.eta} onGround={aircraft.onGround} />
      <dl className="facts">
        <div>
          <dt>Altitude</dt>
          <dd>{aircraft.onGround ? "Au sol" : fmt(aircraft.alt, "m")}</dd>
        </div>
        <div>
          <dt>Vitesse sol</dt>
          <dd>{fmt(aircraft.velocity === null ? null : aircraft.velocity * 3.6, "km/h")}</dd>
        </div>
        <div>
          <dt>Vitesse verticale</dt>
          <dd>{fmt(aircraft.vrate, "m/s", 1)}</dd>
        </div>
        <div>
          <dt>Cap</dt>
          <dd>{fmt(aircraft.track, "°")}</dd>
        </div>
        <div>
          <dt>Squawk</dt>
          <dd>{aircraft.squawk ?? "—"}</dd>
        </div>
        <div>
          <dt>Dernière position</dt>
          <dd>{new Date(aircraft.timePosition * 1000).toISOString().slice(11, 19)} UTC</dd>
        </div>
      </dl>
      {error && <p className="error small">{error}</p>}
    </aside>
  );
}
