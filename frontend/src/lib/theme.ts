import { setPalette, type Theme } from "./colors";

const STORAGE_KEY = "skyradar-theme";

/** Basemap per theme: OpenFreeMap style + background / water tint. */
export const MAP_THEMES: Record<Theme, { style: string; land: string; water: string }> = {
  dark: {
    style: "https://tiles.openfreemap.org/styles/dark",
    land: "#070b12",
    water: "#03060a",
  },
  grey: {
    style: "https://tiles.openfreemap.org/styles/positron",
    land: "#dde1e6",
    water: "#cbd3dc",
  },
};

const THEME_COLOR: Record<Theme, string> = { dark: "#05080d", grey: "#eceff3" };

/** Saved choice, else the system preference (light -> grey). */
export function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "dark" || saved === "grey") return saved;
  } catch {
    // storage blocked (private mode...): fall back to the system preference
  }
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "grey" : "dark";
}

export function applyTheme(theme: Theme, persist = false): void {
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_COLOR[theme]);
  setPalette(theme);
  if (!persist) return;
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // not remembered, still applied
  }
}
