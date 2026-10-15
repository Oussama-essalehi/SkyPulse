import { ALERT_LABELS } from "../lib/labels";
import { AlertKey } from "./AlertKey";
import type { Alert } from "../lib/types";

function ago(epoch: number): string {
  const s = Math.max(0, Math.round(Date.now() / 1000 - epoch));
  if (s < 60) return `il y a ${s} s`;
  if (s < 3600) return `il y a ${Math.round(s / 60)} min`;
  return `il y a ${Math.round(s / 3600)} h`;
}

interface Props {
  alerts: Alert[];
  onPick: (icao24: string) => void;
}

export function AlertsPanel({ alerts, onPick }: Props) {
  return (
    <div className="panel-body">
      <h2>Alertes actives</h2>
      <details className="alert-help">
        <summary>Que signifient les cercles sur la carte ?</summary>
        <p className="small">
          Chaque cercle entoure un avion repéré par le détecteur d'anomalies de SkyRadar. Sa couleur
          indique le niveau :
        </p>
        <AlertKey />
        <p className="small muted">Cliquez sur une alerte pour afficher l'avion concerné.</p>
      </details>
      {alerts.length === 0 && <p className="muted">Aucune anomalie détectée pour le moment.</p>}
      <ul className="alert-list">
        {alerts.map((alert) => (
          <li key={alert.alert_id}>
            <button
              className={`alert alert-${alert.severity}`}
              onClick={() => onPick(alert.icao24)}
            >
              <span className="alert-title">
                <strong>{alert.callsign ?? alert.icao24}</strong>
                <span>{ALERT_LABELS[alert.type] ?? alert.type}</span>
              </span>
              <span className="alert-message">{alert.message}</span>
              <span className="alert-time">{ago(alert.event_time)}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AlertBanner({ alerts, onPick }: Props) {
  const critical = alerts.filter((a) => a.severity === "critical");
  if (critical.length === 0) return null;
  const first = critical[0];
  return (
    <button className="alert-banner" onClick={() => onPick(first.icao24)}>
      <span className="pulse" />
      <strong>{first.callsign ?? first.icao24}</strong>
      <span>{first.message}</span>
      {critical.length > 1 && <span className="more">+{critical.length - 1}</span>}
    </button>
  );
}
