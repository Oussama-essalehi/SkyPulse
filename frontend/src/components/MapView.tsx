import { MapboxOverlay } from "@deck.gl/mapbox";
import { IconLayer, PathLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import type { PickingInfo } from "@deck.gl/core";
import { Map as MapLibreMap, NavigationControl, setWorkerUrl } from "maplibre-gl";
// MapLibre v6 loads its tile worker from a separate ES module: let Vite bundle it.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";

import {
  AIRPORT_LABEL_ZOOM,
  AIRPORT_MIN_ZOOM,
  airportCode,
  labelledAirports,
  visibleAirports,
} from "../lib/airports";
import { ALERT_COLORS, altitudeColor, palette, type Theme } from "../lib/colors";
import { PLANE_ATLAS, PLANE_MAPPING } from "../lib/planeIcon";
import type { AircraftStore, RenderBuffers } from "../lib/store";
import { MAP_THEMES } from "../lib/theme";
import type { Airport, Alert, Filters } from "../lib/types";

setWorkerUrl(maplibreWorkerUrl);

const FRAME_MS = 50; // ~20 fps is plenty for aircraft moving a few pixels per second

export interface FlyTarget {
  lon: number;
  lat: number;
  seq: number;
  zoom?: number;
}

interface Props {
  store: AircraftStore;
  filters: Filters;
  selected: string | null;
  onSelect: (icao24: string | null) => void;
  alerts: Alert[];
  trail: [number, number, number | null, number][] | null;
  flyTo: FlyTarget | null;
  theme: Theme;
  airports: Airport[];
  selectedAirport: string | null;
  onSelectAirport: (icao: string) => void;
}

const AIRPORT_RADIUS: Record<Airport["kind"], number> = { L: 5, M: 3.5, S: 2.5 };

/** Thresholds crossed by the zoom: the airport layers only change when this does. */
function zoomKey(zoom: number): string {
  const kinds = ["L", "M", "S"] as const;
  return kinds
    .map((k) => `${zoom >= AIRPORT_MIN_ZOOM[k] ? 1 : 0}${zoom >= AIRPORT_LABEL_ZOOM[k] ? 1 : 0}`)
    .join("");
}

interface AlertPoint {
  position: [number, number];
  severity: string;
  icao24: string;
}

const TOOLTIP_STYLE: Record<Theme, Record<string, string>> = {
  dark: { background: "rgba(8,13,22,.92)", color: "#e2e8f0", border: "1px solid #1e293b" },
  grey: { background: "rgba(241,243,246,.96)", color: "#1f2933", border: "1px solid #c3cad4" },
};

/** Radar-style tint on top of the OpenFreeMap style of the theme. */
function tintStyle(map: MapLibreMap, theme: Theme): void {
  const { land, water } = MAP_THEMES[theme];
  for (const layer of map.getStyle().layers ?? []) {
    if (layer.type === "background") map.setPaintProperty(layer.id, "background-color", land);
    if (layer.type === "fill" && layer.id.includes("water"))
      map.setPaintProperty(layer.id, "fill-color", water);
  }
}

export function MapView(props: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const propsRef = useRef(props);
  const buffersRef = useRef<RenderBuffers | null>(null);
  // Airports drawn / labelled at the current zoom, recomputed when a threshold is crossed.
  const airportCache = useRef<{
    key: string;
    source: Airport[];
    dots: Airport[];
    labels: Airport[];
  } | null>(null);

  useEffect(() => {
    propsRef.current = props;
  });

  useEffect(() => {
    if (!container.current) return;
    const map = new MapLibreMap({
      container: container.current,
      style: MAP_THEMES[propsRef.current.theme].style,
      center: [8, 46],
      zoom: 3.6,
      minZoom: 1.5,
      maxPitch: 0,
      dragRotate: false,
      attributionControl: { compact: true },
    });
    map.touchZoomRotate.disableRotation();
    map.addControl(new NavigationControl({ showCompass: false }), "bottom-right");
    map.on("style.load", () => tintStyle(map, propsRef.current.theme));
    mapRef.current = map;

    const overlay = new MapboxOverlay({
      interleaved: false,
      pickingRadius: 6,
      getTooltip: ({ layer, index, object }: PickingInfo) => {
        if (layer?.id === "airports" && object) {
          const ap = object as Airport;
          return {
            text: `${airportCode(ap)} · ${ap.name}`,
            style: {
              ...TOOLTIP_STYLE[propsRef.current.theme],
              fontSize: "12px",
              borderRadius: "6px",
            },
          };
        }
        if (layer?.id !== "aircraft" || index < 0) return null;
        const id = buffersRef.current?.ids[index];
        const a = id ? propsRef.current.store.aircraft.get(id) : undefined;
        if (!a) return null;
        const alt = a.onGround ? "au sol" : a.alt !== null ? `${Math.round(a.alt)} m` : "?";
        return {
          text: `${a.callsign ?? a.icao24} · ${alt}`,
          style: {
            ...TOOLTIP_STYLE[propsRef.current.theme],
            fontSize: "12px",
            borderRadius: "6px",
          },
        };
      },
    });
    map.addControl(overlay);
    map.on("click", (event) => {
      // Clicks on deck layers are handled by onClick; this only clears selection.
      const picked = overlay.pickObject({ x: event.point.x, y: event.point.y, radius: 6 });
      if (!picked) propsRef.current.onSelect(null);
    });

    let raf = 0;
    let last = 0;
    const render = (time: number) => {
      raf = requestAnimationFrame(render);
      if (time - last < FRAME_MS) return;
      last = time;
      const { store, filters, selected, alerts, trail, onSelect } = propsRef.current;
      const { airports, selectedAirport, onSelectAirport } = propsRef.current;
      const key = zoomKey(map.getZoom());
      let cache = airportCache.current;
      if (!cache || cache.key !== key || cache.source !== airports) {
        const zoom = map.getZoom();
        cache = {
          key,
          source: airports,
          dots: visibleAirports(airports, zoom),
          labels: labelledAirports(airports, zoom),
        };
        airportCache.current = cache;
      }
      const highlighted = selectedAirport
        ? airports.find((a) => a.icao === selectedAirport)
        : undefined;
      const colors = palette();
      const buffers = store.frame(filters, selected);
      buffersRef.current = buffers;
      const now = store.now();
      const selectedAircraft = selected ? store.aircraft.get(selected) : undefined;
      const selectedPos = selectedAircraft ? store.position(selectedAircraft, now) : null;

      const alertPoints: AlertPoint[] = [];
      for (const alert of alerts) {
        const a = store.aircraft.get(alert.icao24);
        // Live aircraft: follow it; otherwise the position stored with the alert, if any.
        if (a)
          alertPoints.push({
            position: store.position(a, now),
            severity: alert.severity,
            icao24: alert.icao24,
          });
        else if (alert.longitude !== null && alert.latitude !== null)
          alertPoints.push({
            position: [alert.longitude, alert.latitude],
            severity: alert.severity,
            icao24: alert.icao24,
          });
      }
      const pulse = (time % 1600) / 1600;

      const layers = [
        new ScatterplotLayer<Airport>({
          id: "airports",
          data: cache.dots,
          getPosition: (a) => [a.lon, a.lat],
          getRadius: (a) => AIRPORT_RADIUS[a.kind],
          radiusUnits: "pixels",
          stroked: true,
          filled: true,
          getFillColor: colors.airportFill,
          getLineColor: colors.airport,
          getLineWidth: 1.5,
          lineWidthUnits: "pixels",
          pickable: true,
          onClick: (info: PickingInfo) => {
            const ap = info.object as Airport | undefined;
            if (ap) onSelectAirport(ap.icao);
            return true;
          },
        }),
        new TextLayer<Airport>({
          id: "airport-labels",
          data: cache.labels,
          getPosition: (a) => [a.lon, a.lat],
          getText: (a) => airportCode(a),
          getSize: 11,
          getColor: colors.airport,
          getTextAnchor: "start",
          getAlignmentBaseline: "center",
          getPixelOffset: [8, 0],
          fontFamily: "Inter, system-ui, sans-serif",
          fontWeight: 600,
          fontSettings: { sdf: true },
          outlineWidth: 3,
          outlineColor: [...colors.airportFill, 220],
          pickable: false,
        }),
        highlighted
          ? new ScatterplotLayer<Airport>({
              id: "airport-selection",
              data: [highlighted],
              getPosition: (a) => [a.lon, a.lat],
              getRadius: 13,
              radiusUnits: "pixels",
              stroked: true,
              filled: false,
              getLineWidth: 2.5,
              lineWidthUnits: "pixels",
              getLineColor: [...colors.selected, 255],
            })
          : null,
        trail && selectedPos
          ? new PathLayer({
              id: "trail",
              data: [{ path: [...trail.map((p) => [p[0], p[1]]), selectedPos] }],
              getPath: (d: { path: number[][] }) => d.path as [number, number][],
              getColor: palette().trail,
              getWidth: 2,
              widthUnits: "pixels",
              jointRounded: true,
              capRounded: true,
            })
          : null,
        new ScatterplotLayer<AlertPoint>({
          id: "alerts",
          data: alertPoints,
          getPosition: (d) => d.position,
          getRadius: 10 + pulse * 22,
          radiusUnits: "pixels",
          stroked: true,
          filled: false,
          lineWidthUnits: "pixels",
          getLineWidth: 2,
          getLineColor: (d) => [
            ...(ALERT_COLORS[d.severity] ?? ALERT_COLORS.info),
            255 * (1 - pulse),
          ],
          updateTriggers: { getRadius: pulse, getLineColor: pulse },
          pickable: false,
        }),
        new IconLayer({
          id: "aircraft",
          data: {
            length: buffers.length,
            attributes: {
              getPosition: { value: buffers.positions, size: 2 },
              getAngle: { value: buffers.angles, size: 1 },
              getColor: { value: buffers.colors, size: 4, normalized: true },
            },
          },
          iconAtlas: PLANE_ATLAS,
          iconMapping: PLANE_MAPPING,
          getIcon: () => "plane",
          getSize: 18,
          sizeUnits: "pixels",
          sizeMinPixels: 9,
          sizeMaxPixels: 26,
          billboard: false,
          pickable: true,
          onClick: (info: PickingInfo) => {
            const id = buffersRef.current?.ids[info.index];
            if (id) onSelect(id);
            return true;
          },
        }),
        selectedPos && selectedAircraft
          ? new ScatterplotLayer({
              id: "selection",
              data: [selectedPos],
              getPosition: (d: [number, number]) => d,
              getRadius: 16,
              radiusUnits: "pixels",
              stroked: true,
              filled: false,
              getLineWidth: 2,
              lineWidthUnits: "pixels",
              getLineColor: [
                ...altitudeColor(selectedAircraft.alt, selectedAircraft.onGround),
                255,
              ] as [number, number, number, number],
            })
          : null,
      ];
      overlay.setProps({ layers });
    };
    raf = requestAnimationFrame(render);

    return () => {
      cancelAnimationFrame(raf);
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Theme switch: new basemap (deck.gl layers live on their own canvas and are kept).
  const { theme } = props;
  const appliedTheme = useRef(theme);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || appliedTheme.current === theme) return;
    appliedTheme.current = theme;
    map.setStyle(MAP_THEMES[theme].style);
  }, [theme]);

  const { flyTo } = props;
  useEffect(() => {
    if (flyTo && mapRef.current) {
      mapRef.current.flyTo({
        center: [flyTo.lon, flyTo.lat],
        zoom: Math.max(mapRef.current.getZoom(), flyTo.zoom ?? 6.5),
        speed: 1.6,
      });
    }
  }, [flyTo]);

  return <div ref={container} className="map" />;
}
