import { ALTITUDE_STOPS, GROUND_COLOR, rgbCss } from "../lib/colors";

export function Legend() {
  const gradient = ALTITUDE_STOPS.map(
    ([alt, color]) => `${rgbCss(color)} ${(alt / 13000) * 100}%`,
  ).join(", ");
  return (
    <div className="legend" aria-label="Légende des altitudes">
      <span className="legend-ground" style={{ background: rgbCss(GROUND_COLOR) }} title="Au sol" />
      <div className="legend-bar" style={{ background: `linear-gradient(90deg, ${gradient})` }} />
      <div className="legend-labels">
        <span>0</span>
        <span>4 000</span>
        <span>8 000</span>
        <span>13 000 m</span>
      </div>
    </div>
  );
}
