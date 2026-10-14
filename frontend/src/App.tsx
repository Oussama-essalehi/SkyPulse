import { useCallback, useEffect, useMemo, useState } from "react";

import { AircraftPanel } from "./components/AircraftPanel";
import { AlertBanner, AlertsPanel } from "./components/AlertsPanel";
import { AssistantPanel } from "./components/AssistantPanel";
import { FiltersPanel } from "./components/FiltersPanel";
import { Legend } from "./components/Legend";
import { type FlyTarget, MapView } from "./components/MapView";
import { type PanelName, TopBar } from "./components/TopBar";
import { api } from "./lib/api";
import type { Theme } from "./lib/colors";
import { DEFAULT_FILTERS } from "./lib/labels";
import { applyTheme, initialTheme } from "./lib/theme";
import type { AircraftDetail, Filters } from "./lib/types";
import { useLive } from "./lib/useLive";

const DETAIL_REFRESH_MS = 15_000;

export default function App() {
  const { store, status, alerts, tick } = useLive();
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [selected, setSelected] = useState<string | null>(null);
  const [panel, setPanel] = useState<PanelName>(null);
  const [detail, setDetail] = useState<AircraftDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [flyTo, setFlyTo] = useState<FlyTarget | null>(null);
  const [theme, setTheme] = useState<Theme>(() => {
    const initial = initialTheme();
    applyTheme(initial); // before the first paint: no flash of the wrong theme
    return initial;
  });
  const changeTheme = useCallback((next: Theme) => {
    applyTheme(next, true);
    setTheme(next);
  }, []);

  // Fetch the aircraft card (trail, alerts, reconstructed flight) and keep it fresh.
  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    const load = () =>
      api
        .aircraft(selected)
        .then((d) => {
          if (!cancelled) {
            setDetail(d);
            setDetailError(null);
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

  const pick = useCallback(
    (icao24: string) => {
      setSelected(icao24);
      const a = store.aircraft.get(icao24);
      if (a) {
        const [lon, lat] = store.position(a, store.now());
        setFlyTo({ lon, lat, seq: Date.now() });
      }
    },
    [store],
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
  const trail = detail?.state.icao24 === selected ? (detail?.trail ?? null) : null;

  return (
    <div className="app">
      <MapView
        store={store}
        filters={filters}
        selected={selected}
        onSelect={setSelected}
        alerts={alerts}
        trail={trail}
        flyTo={flyTo}
        theme={theme}
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
        theme={theme}
        onTheme={changeTheme}
      />
      <AlertBanner alerts={alerts} onPick={pick} />
      {panel && (
        <section className="side-panel">
          <button className="close" onClick={() => setPanel(null)} aria-label="Fermer">
            ×
          </button>
          {panel === "filters" && (
            <FiltersPanel filters={filters} countries={countries} onChange={setFilters} />
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
          onClose={() => setSelected(null)}
        />
      )}
      <Legend theme={theme} />
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
