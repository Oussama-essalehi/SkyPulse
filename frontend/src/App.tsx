import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AircraftPanel } from "./components/AircraftPanel";
import { AirportPanel } from "./components/AirportPanel";
import { AlertBanner, AlertsPanel } from "./components/AlertsPanel";
import { AssistantPanel } from "./components/AssistantPanel";
import { FiltersPanel } from "./components/FiltersPanel";
import { Legend } from "./components/Legend";
import { type FitTarget, type FlyTarget, MapView } from "./components/MapView";
import { type PanelName, TopBar } from "./components/TopBar";
import { fromColumns } from "./lib/airports";
import { api } from "./lib/api";
import { flightFrame } from "./lib/flight";
import { DEFAULT_FILTERS } from "./lib/labels";
import type { AircraftDetail, Airport, AirportBoard, Filters } from "./lib/types";
import { useLive } from "./lib/useLive";

const DETAIL_REFRESH_MS = 15_000;
const BOARD_REFRESH_MS = 30_000;
const AIRPORTS_RETRY_MS = 60_000;

export default function App() {
  const { store, status, alerts, tick } = useLive();
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [selected, setSelected] = useState<string | null>(null);
  const [panel, setPanel] = useState<PanelName>(null);
  const [detail, setDetail] = useState<AircraftDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [flyTo, setFlyTo] = useState<FlyTarget | null>(null);
  const [fitTo, setFitTo] = useState<FitTarget | null>(null);
  // Aircraft whose whole flight was already framed (once per selection, not every refresh).
  const framed = useRef<string | null>(null);
  const [airports, setAirports] = useState<Airport[]>([]);
  const [airport, setAirport] = useState<string | null>(null);
  // Airport board left for one of its aircraft: the aircraft card links back to it.
  const [backTo, setBackTo] = useState<string | null>(null);
  const [board, setBoard] = useState<AirportBoard | null>(null);
  const [boardError, setBoardError] = useState<string | null>(null);

  // Airports of the reference (map layer, search); retried while the API has none yet.
  useEffect(() => {
    let timer = 0;
    const load = () =>
      api
        .airports()
        .then((c) => {
          setAirports(fromColumns(c));
          if (c.icao.length === 0) timer = window.setTimeout(load, AIRPORTS_RETRY_MS);
        })
        .catch(() => {
          timer = window.setTimeout(load, AIRPORTS_RETRY_MS);
        });
    void load();
    return () => window.clearTimeout(timer);
  }, []);
  const airportsByIcao = useMemo(() => new Map(airports.map((a) => [a.icao, a])), [airports]);

  // Live board of the selected airport, refreshed while it is open.
  useEffect(() => {
    if (!airport) return;
    let cancelled = false;
    const load = () =>
      api
        .airportBoard(airport)
        .then((b) => {
          if (!cancelled) {
            setBoard(b);
            setBoardError(null);
          }
        })
        .catch((e: Error) => !cancelled && setBoardError(e.message));
    void load();
    const timer = window.setInterval(load, BOARD_REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      setBoard(null);
      setBoardError(null);
    };
  }, [airport]);

  // Airport filter: the map shows the aircraft of the airport's live board (all of them).
  const filterAirport = filters.status === "airport" ? filters.airport : null;
  useEffect(() => {
    if (!filterAirport) return;
    let cancelled = false;
    const load = () =>
      api
        .airportBoard(filterAirport, 500)
        .then((b) => {
          if (cancelled) return;
          const ids = new Set([...b.arrivals, ...b.departures, ...b.ground].map((r) => r.icao24));
          setFilters((f) => (f.airport === filterAirport ? { ...f, aircraft: ids } : f));
        })
        .catch(() => {
          // kept as is: the next refresh tries again
        });
    void load();
    const timer = window.setInterval(load, BOARD_REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [filterAirport]);

  // Fetch the aircraft card (trail, alerts, reconstructed flight) and keep it fresh.
  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    const load = () =>
      api
        .aircraft(selected)
        .then((d) => {
          if (cancelled) return;
          setDetail(d);
          setDetailError(null);
          // First load of this aircraft: frame its whole flight.
          if (framed.current !== d.state.icao24) {
            framed.current = d.state.icao24;
            const bounds = flightFrame(d);
            if (bounds) setFitTo({ bounds, seq: Date.now() });
          }
        })
        .catch((e: Error) => !cancelled && setDetailError(e.message));
    void load();
    const timer = window.setInterval(load, DETAIL_REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      setDetail(null);
    };
  }, [selected]);

  /** Select an aircraft (null: clear every selection, e.g. a click on the empty map). */
  const select = useCallback((icao24: string | null) => {
    setSelected(icao24);
    framed.current = null;
    setAirport(null);
    setBackTo(null);
  }, []);

  const pick = useCallback(
    (icao24: string) => {
      select(icao24);
      const a = store.aircraft.get(icao24);
      if (a) {
        const [lon, lat] = store.position(a, store.now());
        setFlyTo({ lon, lat, seq: Date.now() });
      }
    },
    [store, select],
  );

  const pickAirport = useCallback(
    (icao: string, fly = true) => {
      setSelected(null);
      setBackTo(null);
      setAirport(icao);
      const a = airportsByIcao.get(icao);
      if (a && fly) setFlyTo({ lon: a.lon, lat: a.lat, seq: Date.now(), zoom: 8.5 });
    },
    [airportsByIcao],
  );

  /** From the board to one of its aircraft, keeping a way back. */
  const pickFromBoard = useCallback(
    (icao24: string) => {
      const from = airport;
      pick(icao24);
      setBackTo(from);
    },
    [airport, pick],
  );

  // Recomputed when a new message arrives (tick), not on every animation frame.
  const visible = useMemo(() => {
    void tick;
    let n = 0;
    for (const a of store.aircraft.values()) if (store.matches(a, filters)) n++;
    return n;
  }, [store, filters, tick]);
  const countries = useMemo(() => (panel === "filters" ? store.countries() : []), [store, panel]);
  const criticalCount = alerts.filter((a) => a.severity === "critical").length;
  const current = detail?.state.icao24 === selected ? detail : null;

  return (
    <div className="app">
      <MapView
        store={store}
        filters={filters}
        selected={selected}
        onSelect={select}
        airports={airports}
        selectedAirport={airport}
        onSelectAirport={(icao) => pickAirport(icao, false)}
        alerts={alerts}
        trail={current?.trail ?? null}
        flight={current?.flight ?? null}
        flyTo={flyTo}
        fitTo={fitTo}
      />
      <TopBar
        store={store}
        status={status}
        visible={visible}
        alertCount={alerts.length}
        criticalCount={criticalCount}
        panel={panel}
        onPanel={setPanel}
        onPick={pick}
        airports={airports}
        onPickAirport={pickAirport}
      />
      <AlertBanner alerts={alerts} onPick={pick} />
      {panel && (
        <section className="side-panel">
          <button className="close" onClick={() => setPanel(null)} aria-label="Fermer">
            ×
          </button>
          {panel === "filters" && (
            <FiltersPanel
              filters={filters}
              countries={countries}
              airports={airports}
              onChange={setFilters}
              onAirport={(icao) => {
                const a = airportsByIcao.get(icao);
                if (a) setFlyTo({ lon: a.lon, lat: a.lat, seq: Date.now(), zoom: 8 });
              }}
            />
          )}
          {panel === "alerts" && <AlertsPanel alerts={alerts} onPick={pick} />}
          {panel === "assistant" && <AssistantPanel />}
        </section>
      )}
      {selected && (
        <AircraftPanel
          aircraft={store.aircraft.get(selected)}
          detail={detail}
          error={detailError}
          onClose={() => select(null)}
          back={
            backTo
              ? {
                  label: `Tableau ${airportsByIcao.get(backTo)?.iata ?? backTo}`,
                  onBack: () => pickAirport(backTo, false),
                }
              : undefined
          }
        />
      )}
      {airport && (
        <AirportPanel
          airport={airportsByIcao.get(airport)}
          board={board}
          error={boardError}
          now={store.now()}
          onClose={() => setAirport(null)}
          onPick={pickFromBoard}
        />
      )}
      <Legend />
      <footer className="attribution">
        Données ADS-B ©{" "}
        <a href="https://opensky-network.org" target="_blank" rel="noreferrer">
          OpenSky Network
        </a>{" "}
        · usage non commercial
      </footer>
    </div>
  );
}
