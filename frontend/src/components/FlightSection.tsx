import { airportLabel, clock, duration } from "../lib/flight";
import type { EtaInfo, FlightInfo } from "../lib/types";

interface Props {
  flight: FlightInfo | null | undefined;
  eta: EtaInfo | null | undefined;
  onGround: boolean;
}

function Time({ epoch, estimated }: { epoch: number; estimated?: boolean }) {
  const { utc, local } = clock(epoch);
  return (
    <>
      <strong>
        {estimated ? "≈ " : ""}
        {utc}
      </strong>
      {local && <span className="muted"> · {local} chez vous</span>}
    </>
  );
}

export function FlightSection({ flight, eta, onGround }: Props) {
  if (!flight || (!flight.departure && !flight.arrival)) {
    return (
      <section className="flight">
        <h3>Vol</h3>
        <p className="muted small">
          Route inconnue pour ce callsign (vol privé, militaire ou non publié). SkyRadar la
          reconstitue à partir de l'historique après quelques heures de données.
        </p>
        {flight?.rejected_route && (
          <p className="muted small">
            Route publiée {flight.rejected_route} ignorée : incohérente avec la position.
          </p>
        )}
      </section>
    );
  }
  const from = airportLabel(flight.departure);
  const to = airportLabel(flight.arrival);
  const progress = flight.progress ?? null;
  return (
    <section className="flight">
      <h3>Vol</h3>
      <div className="route-ends">
        <div className="route-end" title={flight.departure?.name ?? undefined}>
          <span className="route-code">{from.code}</span>
          <span className="route-place">{from.place}</span>
          {flight.departure?.country && (
            <span className="muted small">{flight.departure.country}</span>
          )}
        </div>
        <span className="route-plane" aria-hidden="true">
          ✈
        </span>
        <div className="route-end route-end-right" title={flight.arrival?.name ?? undefined}>
          <span className="route-code">{to.code}</span>
          <span className="route-place">{to.place}</span>
          {flight.arrival?.country && <span className="muted small">{flight.arrival.country}</span>}
        </div>
      </div>
      {progress !== null && (
        <div
          className="route-progress"
          role="progressbar"
          aria-valuenow={Math.round(progress * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Progression du vol"
        >
          <div className="route-progress-bar" style={{ width: `${progress * 100}%` }} />
        </div>
      )}
      <dl className="flight-times">
        {flight.departure_time !== null && (
          <div>
            <dt>Départ</dt>
            <dd>
              <Time epoch={flight.departure_time} estimated={flight.departure_time_estimated} />
            </dd>
          </div>
        )}
        {eta && !onGround && (
          <div>
            <dt>Arrivée estimée</dt>
            <dd>
              <Time epoch={eta.arrival_estimate} />
            </dd>
          </div>
        )}
        {flight.elapsed_s !== null && (
          <div>
            <dt>Temps de vol</dt>
            <dd>{duration(flight.elapsed_s)}</dd>
          </div>
        )}
        {eta && !onGround && (
          <div>
            <dt>Temps restant</dt>
            <dd>{duration(eta.seconds)}</dd>
          </div>
        )}
        {flight.route_km !== null && (
          <div>
            <dt>Distance</dt>
            <dd>
              {(flight.flown_km ?? 0).toLocaleString("fr-FR")} /{" "}
              {flight.route_km.toLocaleString("fr-FR")} km
            </dd>
          </div>
        )}
      </dl>
    </section>
  );
}
