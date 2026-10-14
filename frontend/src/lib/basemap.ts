import type { Map as MapLibreMap } from "maplibre-gl";

/** OpenFreeMap "positron" (free, no key), repainted in the mid tones of MAP_COLORS. */
export const MAP_STYLE = "https://tiles.openfreemap.org/styles/positron";

/**
 * Muted map: olive-grey land, slate-teal water, roads a shade lighter than the land,
 * dark labels on a light halo. Nothing is bright, so the colored aircraft stand out.
 */
export const MAP_COLORS = {
  land: "#84837e",
  water: "#5d747a",
  green: "#7e8378",
  urban: "#8a8984",
  building: "#7b7a76",
  buildingLine: "#706f6b",
  ice: "#a7aaa8",
  aeroway: "#8f8e88",
  runway: "#a3a29c",
  road: "#9f9e98",
  roadCasing: "#75746f",
  roadSubtle: "rgba(159, 158, 152, 0.6)",
  rail: "#74736e",
  railDash: "#8c8b86",
  border: "#3f4447",
  label: "#15191b",
  labelHalo: "rgba(176, 175, 168, 0.85)",
  waterLabel: "#e3ecee",
  waterHalo: "rgba(45, 62, 68, 0.8)",
};

type Paint = Record<string, string>;

/** Paint overrides for one layer of the positron style (null: keep the original). */
export function basemapPaint(id: string, type: string): Paint | null {
  const c = MAP_COLORS;
  switch (type) {
    case "background":
      return { "background-color": c.land };
    case "fill":
      if (id.includes("water")) return { "fill-color": c.water };
      if (id.includes("ice") || id.includes("glacier")) return { "fill-color": c.ice };
      if (id.includes("wood") || id.includes("park")) return { "fill-color": c.green };
      if (id.includes("residential")) return { "fill-color": c.urban };
      if (id.includes("building"))
        return { "fill-color": c.building, "fill-outline-color": c.buildingLine };
      if (id.includes("aeroway")) return { "fill-color": c.aeroway };
      if (id.includes("pier")) return { "fill-color": c.land };
      return null;
    case "line":
      if (id.includes("waterway")) return { "line-color": c.water };
      if (id.includes("boundary")) return { "line-color": c.border };
      if (id.includes("casing")) return { "line-color": c.roadCasing };
      if (id.includes("rail")) return { "line-color": id.includes("dash") ? c.railDash : c.rail };
      if (id.includes("subtle")) return { "line-color": c.roadSubtle };
      if (id.includes("aeroway")) return { "line-color": c.runway };
      if (id.includes("pier")) return { "line-color": c.land };
      return { "line-color": c.road };
    case "symbol":
      if (id.startsWith("water"))
        return { "text-color": c.waterLabel, "text-halo-color": c.waterHalo };
      return { "text-color": c.label, "text-halo-color": c.labelHalo };
    default:
      return null;
  }
}

/** Repaint every layer once the style is loaded (a layer that refuses is left as is). */
export function paintBasemap(map: MapLibreMap): void {
  // Property names come from the table above, not from MapLibre's typed union.
  const setPaint = map.setPaintProperty.bind(map) as (id: string, name: string, v: string) => void;
  for (const layer of map.getStyle().layers ?? []) {
    for (const [property, value] of Object.entries(basemapPaint(layer.id, layer.type) ?? {})) {
      try {
        setPaint(layer.id, property, value);
      } catch {
        // property not supported by this layer: keep the style's own color
      }
    }
  }
}
