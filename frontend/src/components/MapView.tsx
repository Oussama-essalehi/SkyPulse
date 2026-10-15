import { MapboxOverlay } from "@deck.gl/mapbox";
import { IconLayer, PathLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import type { PickingInfo } from "@deck.gl/core";
import { type ErrorEvent, Map as MapLibreMap, NavigationControl, setWorkerUrl } from "maplibre-gl";
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
import { MAP_STYLE, paintBasemap } from "../lib/basemap";
import { ALERT_COLORS, altitudeColor, PALETTE } from "../lib/colors";
import { flightPath, routeKnown, type TrailPoint } from "../lib/flight";
import { PLANE_ATLAS, PLANE_MAPPING } from "../lib/planeIcon";
import type { AircraftStore, RenderBuffers } from "../lib/store";
import type { Airport, Alert, Filters, FlightInfo } from "../lib/types";

setWorkerUrl(maplibreWorkerUrl);

const FRAME_MS = 50; // ~20 fps is plenty for aircraft moving a few pixels per second

/** Frame a whole flight (bounds [[west, south], [east, north]]). */
export interface FitTarget {
  bounds: [[number, number], [number, number]];
  seq: number;
}

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
  trail: TrailPoint[] | null;
  /** Flight of the selected aircraft: its track is drawn from the departure airport. */
  flight: FlightInfo | null;
  flyTo: FlyTarget | null;
  fitTo: FitTarget | null;
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

const TOOLTIP_STYLE = {
  background: "rgba(222, 223, 217, 0.97)",
  color: "#1c2023",
  border: "1px solid #aeb2aa",
  fontSize: "12px",
  borderRadius: "6px",
};

/** Icon sizes (px): the dark outline is the same plane drawn larger underneath. */
const PLANE_SIZE = { size: 18, min: 9, max: 26 };
const OUTLINE_PX = 5;

/** Every other segment of a polyline: a dashed look without the deck.gl extensions. */
function dashes(points: [number, number][]): { path: [number, number][] }[] {
  const out: { path: [number, number][] }[] = [];
  for (let i = 0; i + 1 < points.length; i += 2) out.push({ path: [points[i], points[i + 1]] });
  return out;
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
    map.on("style.load", () => paintBasemap(map));
    // Basemap tiles / style from a public CDN: an outage must not break the live map
    // (aircraft are drawn by deck.gl on their own canvas). Without a listener MapLibre
    // reports it as an error; the previous basemap simply stays.
    map.on("error", (event: ErrorEvent) =>
      console.warn("basemap unavailable:", event.error?.message),
    );
    mapRef.current = map;

    const overlay = new MapboxOverlay({
      interleaved: false,
      pickingRadius: 6,
      getTooltip: ({ layer, index, object }: PickingInfo) => {
        if (layer?.id === "airports" && object) {
          const ap = object as Airport;
          return { text: `${airportCode(ap)} · ${ap.name}`, style: TOOLTIP_STYLE };
        }
        if (layer?.id !== "aircraft" || index < 0) return null;
        const id = buffersRef.current?.ids[index];
        const a = id ? propsRef.current.store.aircraft.get(id) : undefined;
        if (!a) return null;
        const alt = a.onGround ? "au sol" : a.alt !== null ? `${Math.round(a.alt)} m` : "?";
        return { text: `${a.callsign ?? a.icao24} · ${alt}`, style: TOOLTIP_STYLE };
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
      const { store, filters, selected, alerts, trail, flight, onSelect } = propsRef.current;
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
      const colors = PALETTE;
      const now = store.now();
      const selectedAircraft = selected ? store.aircraft.get(selected) : undefined;
      // Focus: with an aircraft selected, the others (and their alerts) are hidden so
      // that its track stays readable over thousands of aircraft.
      const focus = selectedAircraft !== undefined;
      const buffers = store.frame(filters, selected, focus);
      buffersRef.current = buffers;
      const selectedPos = selectedAircraft ? store.position(selectedAircraft, now) : null;
      // No line at all when the route is unknown (private, military, unpublished).
      const path =
        selectedPos && routeKnown(flight) ? flightPath(trail, flight, selectedPos) : null;
      // Departure and destination of the selected flight, always marked and named.
      const ends = (focus ? [flight?.departure, flight?.arrival] : [])
        .filter((a) => a && a.latitude !== null && a.longitude !== null)
        .map((a) => ({
          position: [a!.longitude!, a!.latitude!] as [number, number],
          code: a!.iata ?? a!.icao,
        }));

      const alertPoints: AlertPoint[] = [];
      for (const alert of alerts) {
        if (focus && alert.icao24 !== selected) continue;
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
          getFillColor: colors.airport,
          getLineColor: colors.airportRing,
          getLineWidth: 1,
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
          outlineColor: [...colors.airportRing, 220],
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
        // Track of the selected aircraft: dashed from the departure airport to the first
        // observed position (estimated), then solid (observed), on a dark casing; the
        // rest of the route to the destination dotted, lighter (planned).
        ...(path
          ? [
              new PathLayer<{ path: [number, number][] }>({
                id: "route-ahead",
                data: dashes(path.toArrival ?? []),
                getPath: (d) => d.path,
                getColor: [255, 255, 255, 200],
                getWidth: 2,
                widthUnits: "pixels",
                capRounded: true,
              }),
              new PathLayer<{ path: [number, number][] }>({
                id: "track-casing",
                data: [{ path: path.observed }, ...dashes(path.fromDeparture ?? [])],
                getPath: (d) => d.path,
                getColor: colors.trailCasing,
                getWidth: 5,
                widthUnits: "pixels",
                jointRounded: true,
                capRounded: true,
              }),
              new PathLayer<{ path: [number, number][] }>({
                id: "track",
                data: [{ path: path.observed }, ...dashes(path.fromDeparture ?? [])],
                getPath: (d) => d.path,
                getColor: colors.trail,
                getWidth: 2.5,
                widthUnits: "pixels",
                jointRounded: true,
                capRounded: true,
              }),
            ]
          : []),
        new ScatterplotLayer<{ position: [number, number]; code: string }>({
          id: "route-ends",
          data: ends,
          getPosition: (d) => d.position,
          getRadius: 7,
          radiusUnits: "pixels",
          stroked: true,
          filled: true,
          getFillColor: [255, 255, 255, 255],
          getLineColor: [...colors.outline, 255],
          getLineWidth: 2.5,
          lineWidthUnits: "pixels",
        }),
        new TextLayer<{ position: [number, number]; code: string }>({
          id: "route-end-labels",
          data: ends,
          getPosition: (d) => d.position,
          getText: (d) => d.code,
          getSize: 14,
          getColor: [...colors.outline, 255],
          getTextAnchor: "start",
          getAlignmentBaseline: "center",
          getPixelOffset: [12, 0],
          fontFamily: "Inter, system-ui, sans-serif",
          fontWeight: 700,
          fontSettings: { sdf: true },
          outlineWidth: 4,
          outlineColor: [255, 255, 255, 230],
        }),
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
          id: "aircraft-outline",
          data: {
            length: buffers.length,
            attributes: {
              getPosition: { value: buffers.positions, size: 2 },
              getAngle: { value: buffers.angles, size: 1 },
            },
          },
          iconAtlas: PLANE_ATLAS,
          iconMapping: PLANE_MAPPING,
          getIcon: () => "plane",
          getColor: [...colors.outline, 235],
          getSize: PLANE_SIZE.size + OUTLINE_PX,
          sizeUnits: "pixels",
          sizeMinPixels: PLANE_SIZE.min + OUTLINE_PX,
          sizeMaxPixels: PLANE_SIZE.max + OUTLINE_PX,
          billboard: false,
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
          getSize: PLANE_SIZE.size,
          sizeUnits: "pixels",
          sizeMinPixels: PLANE_SIZE.min,
          sizeMaxPixels: PLANE_SIZE.max,
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

  const { fitTo } = props;
  useEffect(() => {
    const map = mapRef.current;
    if (!fitTo || !map) return;
    // Room for the aircraft card: on the right on a desktop, at the bottom on a phone.
    const phone = window.innerWidth <= 760;
    map.fitBounds(fitTo.bounds, {
      padding: phone
        ? { top: 120, bottom: Math.round(window.innerHeight * 0.55) + 20, left: 30, right: 30 }
        : { top: 90, bottom: 70, left: 70, right: 440 },
      maxZoom: 8,
      duration: 1200,
    });
  }, [fitTo]);

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
