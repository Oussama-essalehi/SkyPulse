import { afterEach, describe, expect, it, vi } from "vitest";

import { altitudeColor, palette, PALETTES, type RGB, setPalette, type Theme } from "./colors";
import { initialTheme, MAP_THEMES } from "./theme";

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

describe("palettes", () => {
  afterEach(() => setPalette("dark"));

  it.each(["dark", "grey"] as Theme[])("%s: aircraft stay visible on land and water", (t) => {
    const { land, water } = MAP_THEMES[t];
    const { stops, ground } = PALETTES[t];
    for (const color of [...stops.map(([, c]) => c), ground]) {
      expect(contrast(color, hex(land))).toBeGreaterThanOrEqual(3);
      expect(contrast(color, hex(water))).toBeGreaterThanOrEqual(3);
    }
  });

  it("switches the active palette", () => {
    const dark = altitudeColor(11000, false);
    setPalette("grey");
    expect(altitudeColor(11000, false)).not.toEqual(dark);
    expect(palette().selected).toEqual([15, 23, 42]);
  });
});

describe("initialTheme", () => {
  afterEach(() => vi.unstubAllGlobals());

  const stub = (saved: string | null, light: boolean, throws = false) => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        if (throws) throw new Error("blocked");
        return saved;
      },
    });
    vi.stubGlobal("window", { matchMedia: () => ({ matches: light }) });
  };

  it("prefers the saved choice", () => {
    stub("grey", false);
    expect(initialTheme()).toBe("grey");
  });

  it("follows the system preference otherwise", () => {
    stub(null, true);
    expect(initialTheme()).toBe("grey");
    stub(null, false);
    expect(initialTheme()).toBe("dark");
  });

  it("survives blocked storage", () => {
    stub(null, true, true);
    expect(initialTheme()).toBe("grey");
  });
});
