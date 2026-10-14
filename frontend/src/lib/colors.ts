export type RGB = [number, number, number];

/**
 * One palette, drawn on a mid-tone basemap (olive-grey land, slate-teal water: see
 * MAP_COLORS). On a mid-tone background no fill color can contrast with both land and
 * water, so every aircraft gets a dark outline: the outline contrasts with the map, the
 * bright fill with the outline (both >= 3:1, WCAG graphics, checked in colors.test.ts).
 */
export const PALETTE = {
  /** Altitude (m) -> color stops, low = warm, high = cold (radar style). */
  stops: [
    [0, [253, 224, 71]],
    [1500, [251, 146, 60]],
    [4000, [244, 63, 94]],
    [7000, [192, 132, 252]],
    [10000, [56, 189, 248]],
    [13000, [94, 234, 212]],
  ] as [number, RGB][],
  ground: [214, 211, 204] as RGB,
  unknown: [203, 213, 225] as RGB,
  selected: [255, 255, 255] as RGB,
  /** Dark halo drawn under every aircraft icon. */
  outline: [17, 21, 24] as RGB,
  /** Observed track of the selected aircraft, and its dark casing. */
  trail: [103, 232, 249, 255] as [number, number, number, number],
  trailCasing: [17, 21, 24, 200] as [number, number, number, number],
  /** Airport dot and label (dark, discreet), and its thin light ring / label halo. */
  airport: [36, 40, 43] as RGB,
  airportRing: [222, 220, 212] as RGB,
};

export type Palette = typeof PALETTE;

export function palette(): Palette {
  return PALETTE;
}

export const ALERT_COLORS: Record<string, RGB> = {
  critical: [239, 68, 68],
  warning: [245, 158, 11],
  info: [56, 189, 248],
};

export function altitudeColor(alt: number | null, onGround: boolean): RGB {
  if (onGround) return PALETTE.ground;
  if (alt === null) return PALETTE.unknown;
  const stops = PALETTE.stops;
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
