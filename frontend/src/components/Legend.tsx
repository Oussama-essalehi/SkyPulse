import { PALETTES, rgbCss, type Theme } from "../lib/colors";

export function Legend({ theme }: { theme: Theme }) {
  const { stops, ground } = PALETTES[theme];
  const gradient = stops
    .map(([alt, color]) => `${rgbCss(color)} ${(alt / 13000) * 100}%`)
    .join(", ");
  return (
    <div className="legend" aria-label="Légende des altitudes">
      <span className="legend-ground" style={{ background: rgbCss(ground) }} title="Au sol" />
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
