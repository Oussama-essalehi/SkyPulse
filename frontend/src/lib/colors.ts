export type RGB = [number, number, number];
export type Theme = "dark" | "grey";

interface Palette {
  /** Altitude (m) -> color stops, low = warm, high = cold (radar style). */
  stops: [number, RGB][];
  ground: RGB;
  unknown: RGB;
  selected: RGB;
  trail: [number, number, number, number];
}

/**
 * The grey theme uses darker stops: the bright yellows and cyans of the dark theme vanish
 * on a light grey map. Every color keeps a contrast >= 3:1 (WCAG, graphics) against its
 * basemap (land and water), checked in theme.test.ts.
 */
export const PALETTES: Record<Theme, Palette> = {
  dark: {
    stops: [
      [0, [253, 224, 71]],
      [1500, [251, 146, 60]],
      [4000, [244, 63, 94]],
      [7000, [192, 132, 252]],
      [10000, [56, 189, 248]],
      [13000, [94, 234, 212]],
    ],
    ground: [100, 116, 139],
    unknown: [148, 163, 184],
    selected: [255, 255, 255],
    trail: [56, 189, 248, 200],
  },
  grey: {
    stops: [
      [0, [161, 98, 7]],
      [1500, [194, 65, 12]],
      [4000, [190, 18, 60]],
      [7000, [126, 34, 206]],
      [10000, [3, 105, 161]],
      [13000, [15, 118, 110]],
    ],
    ground: [71, 85, 105],
    unknown: [71, 85, 105],
    selected: [15, 23, 42],
    trail: [3, 105, 161, 220],
  },
};

let active: Palette = PALETTES.dark;

/** Switch the palette used by the map, the legend and the aircraft card. */
export function setPalette(theme: Theme): void {
  active = PALETTES[theme];
}

export function palette(): Palette {
  return active;
}

export const ALTITUDE_STOPS = PALETTES.dark.stops;
export const GROUND_COLOR: RGB = PALETTES.dark.ground;
export const ALERT_COLORS: Record<string, RGB> = {
  critical: [239, 68, 68],
  warning: [245, 158, 11],
  info: [56, 189, 248],
};

export function altitudeColor(alt: number | null, onGround: boolean, p = active): RGB {
  if (onGround) return p.ground;
  if (alt === null) return p.unknown;
  const stops = p.stops;
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
