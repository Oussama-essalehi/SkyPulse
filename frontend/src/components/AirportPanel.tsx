import { type ReactNode, useState } from "react";

import { airportCode } from "../lib/airports";
import { airportLabel, clock, duration, placeName } from "../lib/flight";
import type { Airport, AirportBoard, BoardRow, BoardStatus, Movement } from "../lib/types";

type Tab = "arrivals" | "departures" | "ground" | "history";

const STATUS_LABEL: Record<BoardStatus, string> = {
  approach: "En approche",
  en_route: "En route",
  climb: "Montée initiale",
  arrived: "Arrivé",
  departing: "Au départ",
  parked: "Au sol",
};

interface Props {
  airport: Airport | undefined;
  board: AirportBoard | null;
  error: string | null;
  now: number;
  onClose: () => void;
  onPick: (icao24: string) => void;
}

function Other({ row, prefix }: { row: BoardRow; prefix: string }) {
  if (!row.airport) return <span className="muted">{prefix} ?</span>;
  const { code, place } = airportLabel(row.airport);
  return (
    <span title={row.airport.name ?? undefined}>
      {prefix} <strong>{code}</strong> {place}
    </span>
  );
}

function Row({
  row,
  children,
  onPick,
}: {
  row: BoardRow;
  children: ReactNode;
  onPick: (icao24: string) => void;
}) {
  return (
    <li>
      <button className="board-row" onClick={() => onPick(row.icao24)}>
        <span className="board-callsign">
          <strong>{row.callsign ?? row.icao24.toUpperCase()}</strong>
          {row.airline && <span className="muted small">{row.airline}</span>}
        </span>
        {children}
      </button>
    </li>
  );
}

function ArrivalRow({
  row,
  now,
  onPick,
}: {
  row: BoardRow;
  now: number;
  onPick: (id: string) => void;
}) {
  const eta = row.eta;
  return (
    <Row row={row} onPick={onPick}>
      <span className="board-route">
        <Other row={row} prefix="de" />
        <span className={`board-chip board-chip-${row.status}`}>{STATUS_LABEL[row.status]}</span>
      </span>
      <span className="board-time">
        {eta ? (
          <>
            <strong>
              {eta.arrival_estimate - now > 30
                ? `dans ${duration(eta.arrival_estimate - now)}`
                : "Atterri"}
            </strong>
            <span className="muted small">{clock(eta.arrival_estimate).utc}</span>
          </>
        ) : (
          <span className="muted small">{Math.round(row.distance_km)} km</span>
        )}
      </span>
    </Row>
  );
}

function DepartureRow({
  row,
  now,
  onPick,
}: {
  row: BoardRow;
  now: number;
  onPick: (id: string) => void;
}) {
  const at = row.departure_time;
  return (
    <Row row={row} onPick={onPick}>
      <span className="board-route">
        <Other row={row} prefix="vers" />
        <span className={`board-chip board-chip-${row.status}`}>{STATUS_LABEL[row.status]}</span>
      </span>
      <span className="board-time">
        {at ? (
          <>
            <strong>
              {row.departure_time_estimated ? "≈ " : ""}
              {clock(at).utc}
            </strong>
            <span className="muted small">il y a {duration(Math.max(0, now - at))}</span>
          </>
        ) : (
          <span className="muted small">{Math.round(row.distance_km)} km</span>
        )}
      </span>
    </Row>
  );
}

function GroundRow({ row, onPick }: { row: BoardRow; onPick: (id: string) => void }) {
  return (
    <Row row={row} onPick={onPick}>
      <span className="board-route">
        {row.status === "arrived" && <Other row={row} prefix="arrivé de" />}
        {row.status === "departing" && <Other row={row} prefix="départ vers" />}
        <span className={`board-chip board-chip-${row.status}`}>{STATUS_LABEL[row.status]}</span>
      </span>
    </Row>
  );
}

function MovementRow({ m }: { m: Movement }) {
  const arrival = m.direction === "arrival";
  return (
    <li className="movement">
      <span className="movement-time">{clock(m.time).utc.replace(" UTC", "")}</span>
      <span
        className={`movement-dir ${arrival ? "in" : "out"}`}
        aria-label={arrival ? "Arrivée" : "Départ"}
      >
        {arrival ? "↘" : "↗"}
      </span>
      <strong>{m.callsign ?? "—"}</strong>
      <span className="muted" title={m.airport_name ?? undefined}>
        {arrival ? "de" : "vers"} {m.airport_icao ?? "?"}
      </span>
      {m.typecode && <span className="muted small">{m.typecode}</span>}
    </li>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="muted small board-empty">{children}</p>;
}

export function AirportPanel({ airport, board, error, now, onClose, onPick }: Props) {
  const [tab, setTab] = useState<Tab>("arrivals");
  const info = board?.airport ?? airport;
  const code = info ? airportCode(info) : "…";
  const counts = board?.counts;
  const history = board?.history;
  const tabs: [Tab, string, number | undefined][] = [
    ["arrivals", "Arrivées", counts?.arrivals],
    ["departures", "Départs", counts?.departures],
    ["ground", "Au sol", counts?.ground],
    ["history", "24 h", history ? history.arrivals_24h + history.departures_24h : undefined],
  ];
  return (
    <aside className="aircraft-panel airport-panel" aria-label={`Aéroport ${code}`}>
      <button className="close" onClick={onClose} aria-label="Fermer">
        ×
      </button>
      <header>
        <h2>
          <span className="airport-code">{code}</span> {placeName(info?.city) ?? info?.name}
        </h2>
        <span className="muted">
          {[info?.name, info?.icao, info?.country].filter(Boolean).join(" · ")}
        </span>
      </header>
      <div className="segmented board-tabs" role="tablist">
        {tabs.map(([name, label, n]) => (
          <button
            key={name}
            role="tab"
            aria-selected={tab === name}
            className={tab === name ? "active" : ""}
            onClick={() => setTab(name)}
          >
            {label}
            {n !== undefined && <span className="tab-count">{n}</span>}
          </button>
        ))}
      </div>
      {!board && !error && <p className="muted small">Chargement du tableau…</p>}
      {board && tab === "arrivals" && (
        <ul className="board">
          {board.arrivals.map((r) => (
            <ArrivalRow key={r.icao24} row={r} now={now} onPick={onPick} />
          ))}
          {board.arrivals.length === 0 && <Empty>Aucune arrivée identifiée en ce moment.</Empty>}
        </ul>
      )}
      {board && tab === "departures" && (
        <ul className="board">
          {board.departures.map((r) => (
            <DepartureRow key={r.icao24} row={r} now={now} onPick={onPick} />
          ))}
          {board.departures.length === 0 && <Empty>Aucun départ identifié en ce moment.</Empty>}
        </ul>
      )}
      {board && tab === "ground" && (
        <ul className="board">
          {board.ground.map((r) => (
            <GroundRow key={r.icao24} row={r} onPick={onPick} />
          ))}
          {board.ground.length === 0 && (
            <Empty>Aucun avion au sol signalé (beaucoup d'aéroports ne relaient pas le sol).</Empty>
          )}
        </ul>
      )}
      {board && tab === "history" && (
        <section className="board-history">
          {history ? (
            <>
              <p>
                <strong>{history.departures_24h}</strong> départs et{" "}
                <strong>{history.arrivals_24h}</strong> arrivées reconstitués sur 24 h.
              </p>
              {history.top_airports.length > 0 && (
                <p className="muted small">
                  Liaisons principales :{" "}
                  {history.top_airports.map((a) => `${a.icao} (${a.flights})`).join(", ")}
                </p>
              )}
              <ul className="movements">
                {history.recent.map((m, i) => (
                  <MovementRow key={`${m.time}-${m.callsign}-${i}`} m={m} />
                ))}
              </ul>
            </>
          ) : (
            <Empty>
              Historique indisponible : les vols reconstitués viennent de la couche analytique
              (profil complet), après quelques heures de données.
            </Empty>
          )}
        </section>
      )}
      {board && (
        <p className="muted small board-note">
          Routes publiées vérifiées contre la position, décollages observés, historique, ou à défaut
          géométrie (approche, montée) à moins de 60 km.
          {board.pending_lookups > 0 &&
            ` ${board.pending_lookups} routes encore en recherche, le tableau se complète.`}
        </p>
      )}
      {error && <p className="error small">{error}</p>}
    </aside>
  );
}
