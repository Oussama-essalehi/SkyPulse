import { altitudeColor, rgbCss } from "../lib/colors";
import type { Aircraft, AircraftDetail } from "../lib/types";
import { ALERT_LABELS } from "../lib/labels";

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

function eta(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h > 0 ? `${h} h ${m} min` : `${m} min`;
}

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
      </header>
      {detail?.alerts.map((alert) => (
        <div key={alert.alert_id} className={`alert alert-${alert.severity}`}>
          <strong>{ALERT_LABELS[alert.type] ?? alert.type}</strong>
          <span className="alert-message">{alert.message}</span>
        </div>
      ))}
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
      <section className="flight">
        <h3>Vol</h3>
        <div className="route">
          <span>{flight?.departure ?? "?"}</span>
          <span className="arrow">→</span>
          <span>{flight?.arrival ?? "?"}</span>
        </div>
        <p className="muted small">
          {detail?.eta
            ? `ETA ${eta(detail.eta.seconds)} (${detail.eta.method})`
            : "Départ / arrivée reconstitués par le pipeline à partir de l'historique (disponible après accumulation de données)."}
        </p>
      </section>
      {error && <p className="error small">{error}</p>}
    </aside>
  );
}
