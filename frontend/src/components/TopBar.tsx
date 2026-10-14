import { useState } from "react";

import { airportCode, searchAirports } from "../lib/airports";
import type { AircraftStore } from "../lib/store";
import type { Airport } from "../lib/types";
import type { ConnectionStatus } from "../lib/useLive";

export type PanelName = "alerts" | "filters" | null;

interface Props {
  store: AircraftStore;
  status: ConnectionStatus;
  visible: number;
  alertCount: number;
  criticalCount: number;
  panel: PanelName;
  onPanel: (panel: PanelName) => void;
  chatOpen: boolean;
  onChat: (open: boolean) => void;
  onPick: (icao24: string) => void;
  airports: Airport[];
  onPickAirport: (icao: string) => void;
}

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  open: "En direct",
  connecting: "Connexion…",
  closed: "Déconnecté",
};

function formatTime(epoch: number | null): string {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toISOString().slice(11, 19) + " UTC";
}

const SOURCE_LABEL: Record<string, string> = {
  opensky: "OpenSky Network",
  replay: "Replay",
};

export function TopBar(props: Props) {
  const { store, status, visible, alertCount, criticalCount, panel, onPanel, onPick } = props;
  const { airports, onPickAirport, chatOpen, onChat } = props;
  const [query, setQuery] = useState("");
  const hits = store.search(query, 6);
  const airportHits = searchAirports(airports, query);
  const toggle = (name: Exclude<PanelName, null>) => onPanel(panel === name ? null : name);

  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-dot" />
        SkyRadar
      </div>
      <div className="search">
        <input
          type="search"
          placeholder="Vol, icao24 ou aéroport…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Rechercher un avion ou un aéroport"
        />
        {hits.length + airportHits.length > 0 && (
          <ul className="search-results">
            {airportHits.map((a) => (
              <li key={a.icao}>
                <button
                  className="search-airport"
                  onClick={() => {
                    onPickAirport(a.icao);
                    setQuery("");
                  }}
                >
                  <strong>
                    <span className="airport-badge" aria-hidden="true">
                      ◎
                    </span>{" "}
                    {airportCode(a)} · {a.city ?? a.name}
                  </strong>
                  <span>
                    {a.icao} · {a.country}
                  </span>
                </button>
              </li>
            ))}
            {hits.map((a) => (
              <li key={a.icao24}>
                <button
                  onClick={() => {
                    onPick(a.icao24);
                    setQuery("");
                  }}
                >
                  <strong>{a.callsign ?? "—"}</strong>
                  <span>
                    {a.icao24} · {a.country}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="stats">
        <span className="counter" title="Avions affichés / suivis">
          <strong>{visible.toLocaleString("fr-FR")}</strong>
          <span> / {store.aircraft.size.toLocaleString("fr-FR")} avions</span>
        </span>
        <span className="meta" title="Horodatage du dernier snapshot reçu">
          {formatTime(store.dataTime)}
        </span>
        <span className="meta source">
          {SOURCE_LABEL[store.source ?? ""] ?? store.source ?? "—"}
        </span>
        <span className={`status status-${status}`}>
          <span className="status-dot" />
          {STATUS_LABEL[status]}
        </span>
      </div>
      <nav className="actions">
        <button className={panel === "filters" ? "active" : ""} onClick={() => toggle("filters")}>
          Filtres
        </button>
        <button className={panel === "alerts" ? "active" : ""} onClick={() => toggle("alerts")}>
          Alertes
          {alertCount > 0 && (
            <span className={`badge ${criticalCount > 0 ? "badge-critical" : ""}`}>
              {alertCount}
            </span>
          )}
        </button>
        <button
          className={`assistant-toggle ${chatOpen ? "active" : ""}`}
          aria-pressed={chatOpen}
          onClick={() => onChat(!chatOpen)}
        >
          Assistant
        </button>
      </nav>
    </header>
  );
}
