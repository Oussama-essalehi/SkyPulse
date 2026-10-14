import { describe, expect, it } from "vitest";

import { MAP_COLORS, basemapPaint } from "./basemap";
import { altitudeColor, PALETTE, type RGB } from "./colors";

const hex = (h: string): RGB => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB;

function luminance([r, g, b]: RGB): number {
  const ch = (v: number) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
}

function contrast(a: RGB, b: RGB): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const land = hex(MAP_COLORS.land);
const water = hex(MAP_COLORS.water);

describe("palette on the mid-tone basemap", () => {
  it("the aircraft outline and the airport rings stand out from land and water", () => {
    for (const color of [PALETTE.outline, PALETTE.airport]) {
      expect(contrast(color, land)).toBeGreaterThanOrEqual(3);
      expect(contrast(color, water)).toBeGreaterThanOrEqual(3);
    }
  });

  it("every aircraft color stands out from its outline", () => {
    const fills = [...PALETTE.stops.map(([, c]) => c), PALETTE.ground, PALETTE.unknown];
    for (const color of [...fills, PALETTE.selected]) {
      expect(contrast(color, PALETTE.outline)).toBeGreaterThanOrEqual(3);
    }
    const trail = PALETTE.trail.slice(0, 3) as RGB;
    expect(contrast(trail, PALETTE.trailCasing.slice(0, 3) as RGB)).toBeGreaterThanOrEqual(3);
  });

  it("labels are readable on the land", () => {
    expect(contrast(hex(MAP_COLORS.label), land)).toBeGreaterThanOrEqual(4.5);
  });

  it("interpolates altitude colors", () => {
    expect(altitudeColor(0, false)).toEqual(PALETTE.stops[0][1]);
    expect(altitudeColor(20000, false)).toEqual(PALETTE.stops[5][1]);
    expect(altitudeColor(5000, true)).toEqual(PALETTE.ground);
    expect(altitudeColor(null, false)).toEqual(PALETTE.unknown);
  });
});

describe("basemap repaint", () => {
  it("maps positron layers to the muted colors", () => {
    expect(basemapPaint("background", "background")).toEqual({
      "background-color": MAP_COLORS.land,
    });
    expect(basemapPaint("water", "fill")).toEqual({ "fill-color": MAP_COLORS.water });
    expect(basemapPaint("waterway", "line")).toEqual({ "line-color": MAP_COLORS.water });
    expect(basemapPaint("highway_major_casing", "line")).toEqual({
      "line-color": MAP_COLORS.roadCasing,
    });
    expect(basemapPaint("highway_major_inner", "line")).toEqual({ "line-color": MAP_COLORS.road });
    expect(basemapPaint("label_city", "symbol")?.["text-color"]).toBe(MAP_COLORS.label);
    expect(basemapPaint("water_name_point_label", "symbol")?.["text-color"]).toBe(
      MAP_COLORS.waterLabel,
    );
    expect(basemapPaint("highway-shield-us-interstate", "raster")).toBeNull();
  });
});
