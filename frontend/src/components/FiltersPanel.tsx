import { useState } from "react";

import { airportCode, searchAirports } from "../lib/airports";
import { placeName } from "../lib/flight";
import { DEFAULT_FILTERS } from "../lib/labels";
import { MAX_ALTITUDE_FILTER } from "../lib/store";
import type { Airport, Filters } from "../lib/types";
import { WeatherCard } from "./WeatherCard";

interface Props {
  filters: Filters;
  countries: string[];
  airports: Airport[];
  onChange: (filters: Filters) => void;
  /** An airport was chosen in the airport filter (the map flies to it). */
  onAirport: (icao: string) => void;
}

const STATUS = [
  ["all", "Tous"],
  ["airborne", "En vol"],
  ["ground", "Au sol"],
  ["airport", "Aéroport"],
] as const;

function AirportFilter({ filters, airports, onChange, onAirport }: Omit<Props, "countries">) {
  const [query, setQuery] = useState("");
  const chosen = filters.airport ? airports.find((a) => a.icao === filters.airport) : undefined;
  const hits = searchAirports(airports, query, 6);
  const choose = (icao: string) => {
    onChange({ ...filters, airport: icao, aircraft: null });
    onAirport(icao);
    setQuery("");
  };

  if (chosen) {
    const count = filters.aircraft?.size;
    return (
      <div className="airport-filter">
        <div className="airport-filter-chosen">
          <div>
            <strong className="airport-code">{airportCode(chosen)}</strong>{" "}
            {placeName(chosen.city) ?? chosen.name}
            <div className="muted small">
              {chosen.name} · {chosen.icao}
            </div>
          </div>
          <button
            className="link"
            onClick={() => onChange({ ...filters, airport: null, aircraft: null })}
          >
            Changer
          </button>
        </div>
        <p className="muted small">
          {count === undefined
            ? "Recherche des avions de l'aéroport…"
            : `${count} avion${count > 1 ? "s" : ""} : arrivées, départs et au sol.`}
        </p>
        <WeatherCard icao={chosen.icao} />
      </div>
    );
  }
  return (
    <div className="airport-filter">
      <input
        type="search"
        placeholder="Code ou ville : CDG, LFPG, Lyon…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        aria-label="Choisir un aéroport"
        autoFocus
      />
      {hits.length > 0 && (
        <ul className="airport-filter-results">
          {hits.map((a) => (
            <li key={a.icao}>
              <button onClick={() => choose(a.icao)}>
                <strong>{airportCode(a)}</strong> {placeName(a.city) ?? a.name}
                <span className="muted small"> · {a.icao}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {query.trim().length >= 3 && hits.length === 0 && (
        <p className="muted small">Aucun aéroport trouvé.</p>
      )}
    </div>
  );
}

export function FiltersPanel({ filters, countries, airports, onChange, onAirport }: Props) {
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });
  return (
    <div className="panel-body">
      <h2>Filtres</h2>
      <div className="field">
        <span>Statut</span>
        <div className="segmented status-filter">
          {STATUS.map(([value, label]) => (
            <button
              key={value}
              className={filters.status === value ? "active" : ""}
              onClick={() => set({ status: value })}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {filters.status === "airport" && (
        <AirportFilter
          filters={filters}
          airports={airports}
          onChange={onChange}
          onAirport={onAirport}
        />
      )}
      <label className="field">
        <span>
          Altitude min : <strong>{filters.minAlt.toLocaleString("fr-FR")} m</strong>
          {filters.minAlt > 0 && <span className="muted small"> (avions au sol masqués)</span>}
        </span>
        <input
          type="range"
          min={0}
          max={15000}
          step={500}
          value={filters.minAlt}
          onChange={(e) => set({ minAlt: Math.min(Number(e.target.value), filters.maxAlt) })}
        />
      </label>
      <label className="field">
        <span>
          Altitude max :{" "}
          <strong>
            {filters.maxAlt >= MAX_ALTITUDE_FILTER
              ? "illimitée"
              : `${filters.maxAlt.toLocaleString("fr-FR")} m`}
          </strong>
        </span>
        <input
          type="range"
          min={0}
          max={15000}
          step={500}
          value={filters.maxAlt}
          onChange={(e) => set({ maxAlt: Math.max(Number(e.target.value), filters.minAlt) })}
        />
      </label>
      <label className="field">
        <span>Pays d'immatriculation</span>
        <select value={filters.country} onChange={(e) => set({ country: e.target.value })}>
          <option value="">Tous les pays</option>
          {countries.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </label>
      <button className="link" onClick={() => onChange(DEFAULT_FILTERS)}>
        Réinitialiser
      </button>
    </div>
  );
}
