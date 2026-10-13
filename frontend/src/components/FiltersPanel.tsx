import { DEFAULT_FILTERS } from "../lib/labels";
import { MAX_ALTITUDE_FILTER } from "../lib/store";
import type { Filters } from "../lib/types";

interface Props {
  filters: Filters;
  countries: string[];
  onChange: (filters: Filters) => void;
}

export function FiltersPanel({ filters, countries, onChange }: Props) {
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });
  return (
    <div className="panel-body">
      <h2>Filtres</h2>
      <label className="field">
        <span>Statut</span>
        <div className="segmented">
          {(
            [
              ["all", "Tous"],
              ["airborne", "En vol"],
              ["ground", "Au sol"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              className={filters.status === value ? "active" : ""}
              onClick={() => set({ status: value })}
            >
              {label}
            </button>
          ))}
        </div>
      </label>
      <label className="field">
        <span>
          Altitude min : <strong>{filters.minAlt.toLocaleString("fr-FR")} m</strong>
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
