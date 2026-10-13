type RGB = [number, number, number];

/** Altitude (m) -> color stops, low = warm, high = cold (radar style). */
export const ALTITUDE_STOPS: [number, RGB][] = [
  [0, [253, 224, 71]],
  [1500, [251, 146, 60]],
  [4000, [244, 63, 94]],
  [7000, [192, 132, 252]],
  [10000, [56, 189, 248]],
  [13000, [94, 234, 212]],
];

export const GROUND_COLOR: RGB = [100, 116, 139];
export const SELECTED_COLOR: RGB = [255, 255, 255];
export const ALERT_COLORS: Record<string, RGB> = {
  critical: [239, 68, 68],
  warning: [245, 158, 11],
  info: [56, 189, 248],
};

export function altitudeColor(alt: number | null, onGround: boolean): RGB {
  if (onGround) return GROUND_COLOR;
  if (alt === null) return [148, 163, 184];
  const stops = ALTITUDE_STOPS;
  if (alt <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++) {
    const [a1, c1] = stops[i];
    if (alt <= a1) {
      const [a0, c0] = stops[i - 1];
      const t = (alt - a0) / (a1 - a0);
      return [
        Math.round(c0[0] + (c1[0] - c0[0]) * t),
        Math.round(c0[1] + (c1[1] - c0[1]) * t),
        Math.round(c0[2] + (c1[2] - c0[2]) * t),
      ];
    }
  }
  return stops[stops.length - 1][1];
}

export function rgbCss([r, g, b]: RGB): string {
  return `rgb(${r}, ${g}, ${b})`;
}
