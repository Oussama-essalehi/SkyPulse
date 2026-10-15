import { PALETTE, rgbCss } from "../lib/colors";
import { ALERT_LEVELS, AlertRing } from "./AlertKey";

export function Legend() {
  const { stops, ground } = PALETTE;
  const gradient = stops
    .map(([alt, color]) => `${rgbCss(color)} ${(alt / 13000) * 100}%`)
    .join(", ");
  return (
    <div className="legend" aria-label="Légende de la carte">
      <span className="legend-title">Altitude des avions</span>
      <div className="legend-scale">
        <span className="legend-ground" style={{ background: rgbCss(ground) }} title="Au sol" />
        <div className="legend-bar" style={{ background: `linear-gradient(90deg, ${gradient})` }} />
        <div className="legend-labels">
          <span>0</span>
          <span>4 000</span>
          <span>8 000</span>
          <span>13 000 m</span>
        </div>
      </div>
      <span className="legend-title">Cercles : alertes</span>
      <div className="legend-alerts">
        {ALERT_LEVELS.map((level) => (
          <span key={level.severity}>
            <AlertRing severity={level.severity} />
            {level.label}
          </span>
        ))}
      </div>
    </div>
  );
}
