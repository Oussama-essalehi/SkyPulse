import { ALERT_COLORS, rgbCss } from "../lib/colors";

/** What the alert rings on the map mean, for visitors who have never seen ADS-B data. */
export const ALERT_LEVELS = [
  {
    severity: "critical",
    label: "Urgence",
    detail: "l'équipage transmet un code d'urgence (7700) ou de détournement (7500).",
  },
  {
    severity: "warning",
    label: "Anomalie",
    detail:
      "descente rapide, panne radio (7600), déroutement, ou perte de signal en vol (le cercle reste alors à la dernière position connue).",
  },
  {
    severity: "info",
    label: "À surveiller",
    detail: "avion en attente (il tourne en rond avant d'atterrir) ou trajectoire inhabituelle.",
  },
] as const;

export function AlertRing({ severity }: { severity: string }) {
  const color = ALERT_COLORS[severity] ?? ALERT_COLORS.info;
  return <span className="alert-ring" style={{ borderColor: rgbCss(color) }} aria-hidden="true" />;
}

/** The three alert levels, each with its ring color and a plain-language explanation. */
export function AlertKey() {
  return (
    <ul className="alert-key">
      {ALERT_LEVELS.map((level) => (
        <li key={level.severity}>
          <AlertRing severity={level.severity} />
          <span>
            <strong>{level.label}</strong> : {level.detail}
          </span>
        </li>
      ))}
    </ul>
  );
}
