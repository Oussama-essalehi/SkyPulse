import { useState } from "react";

import type { Theme } from "../lib/colors";
import type { AircraftStore } from "../lib/store";
import type { ConnectionStatus } from "../lib/useLive";

export type PanelName = "alerts" | "filters" | "assistant" | null;

interface Props {
  store: AircraftStore;
  status: ConnectionStatus;
  visible: number;
  alertCount: number;
  criticalCount: number;
  panel: PanelName;
  onPanel: (panel: PanelName) => void;
  onPick: (icao24: string) => void;
  theme: Theme;
  onTheme: (theme: Theme) => void;
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
  const { theme, onTheme } = props;
  const [query, setQuery] = useState("");
  const hits = store.search(query);
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
          placeholder="Callsign ou icao24…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Rechercher un avion"
        />
        {hits.length > 0 && (
          <ul className="search-results">
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
          className={panel === "assistant" ? "active" : ""}
          onClick={() => toggle("assistant")}
        >
          Assistant
        </button>
        <button
          className="theme-toggle"
          onClick={() => onTheme(theme === "dark" ? "grey" : "dark")}
          title={theme === "dark" ? "Passer au thème gris" : "Passer au thème sombre"}
          aria-label={theme === "dark" ? "Passer au thème gris" : "Passer au thème sombre"}
        >
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
            <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
            <path d="M8 1.5a6.5 6.5 0 0 1 0 13z" fill="currentColor" />
          </svg>
          {theme === "dark" ? "Gris" : "Sombre"}
        </button>
      </nav>
    </header>
  );
}
