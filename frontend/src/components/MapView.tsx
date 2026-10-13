import { MapboxOverlay } from "@deck.gl/mapbox";
import { IconLayer, PathLayer, ScatterplotLayer } from "@deck.gl/layers";
import type { PickingInfo } from "@deck.gl/core";
import { Map as MapLibreMap, NavigationControl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";

import { ALERT_COLORS, altitudeColor } from "../lib/colors";
import { PLANE_ATLAS, PLANE_MAPPING } from "../lib/planeIcon";
import type { AircraftStore, RenderBuffers } from "../lib/store";
import type { Alert, Filters } from "../lib/types";

const MAP_STYLE = "https://tiles.openfreemap.org/styles/dark";
const FRAME_MS = 50; // ~20 fps is plenty for aircraft moving a few pixels per second

export interface FlyTarget {
  lon: number;
  lat: number;
  seq: number;
}

interface Props {
  store: AircraftStore;
  filters: Filters;
  selected: string | null;
  onSelect: (icao24: string | null) => void;
  alerts: Alert[];
  trail: [number, number, number | null, number][] | null;
  flyTo: FlyTarget | null;
}

interface AlertPoint {
  position: [number, number];
  severity: string;
  icao24: string;
}

/** Radar-style tweaks on top of the OpenFreeMap dark style. */
function tintStyle(map: MapLibreMap): void {
  for (const layer of map.getStyle().layers ?? []) {
    if (layer.type === "background") map.setPaintProperty(layer.id, "background-color", "#070b12");
    if (layer.type === "fill" && layer.id.includes("water"))
      map.setPaintProperty(layer.id, "fill-color", "#03060a");
  }
}

export function MapView(props: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const propsRef = useRef(props);
  const buffersRef = useRef<RenderBuffers | null>(null);

  useEffect(() => {
    propsRef.current = props;
  });

  useEffect(() => {
    if (!container.current) return;
    const map = new MapLibreMap({
      container: container.current,
      style: MAP_STYLE,
      center: [8, 46],
      zoom: 3.6,
      minZoom: 1.5,
      maxPitch: 0,
      dragRotate: false,
      attributionControl: { compact: true },
    });
    map.touchZoomRotate.disableRotation();
    map.addControl(new NavigationControl({ showCompass: false }), "bottom-right");
    map.on("style.load", () => tintStyle(map));
    mapRef.current = map;

    const overlay = new MapboxOverlay({
      interleaved: false,
      pickingRadius: 6,
      getTooltip: ({ layer, index }: PickingInfo) => {
        if (layer?.id !== "aircraft" || index < 0) return null;
        const id = buffersRef.current?.ids[index];
        const a = id ? propsRef.current.store.aircraft.get(id) : undefined;
        if (!a) return null;
        const alt = a.onGround ? "au sol" : a.alt !== null ? `${Math.round(a.alt)} m` : "?";
        return {
          text: `${a.callsign ?? a.icao24} · ${alt}`,
          style: {
            background: "rgba(8,13,22,.92)",
            color: "#e2e8f0",
            fontSize: "12px",
            border: "1px solid #1e293b",
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
      const buffers = store.frame(filters, selected);
      buffersRef.current = buffers;
      const now = store.now();
      const selectedAircraft = selected ? store.aircraft.get(selected) : undefined;
      const selectedPos = selectedAircraft ? store.position(selectedAircraft, now) : null;

      const alertPoints: AlertPoint[] = alerts.map((alert) => {
        const a = store.aircraft.get(alert.icao24);
        const position: [number, number] = a
          ? store.position(a, now)
          : [alert.longitude ?? 0, alert.latitude ?? 0];
        return { position, severity: alert.severity, icao24: alert.icao24 };
      });
      const pulse = (time % 1600) / 1600;

      const layers = [
        trail && selectedPos
          ? new PathLayer({
              id: "trail",
              data: [{ path: [...trail.map((p) => [p[0], p[1]]), selectedPos] }],
              getPath: (d: { path: number[][] }) => d.path as [number, number][],
              getColor: [56, 189, 248, 200],
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

  const { flyTo } = props;
  useEffect(() => {
    if (flyTo && mapRef.current) {
      mapRef.current.flyTo({
        center: [flyTo.lon, flyTo.lat],
        zoom: Math.max(mapRef.current.getZoom(), 6.5),
        speed: 1.6,
      });
    }
  }, [flyTo]);

  return <div ref={container} className="map" />;
}
