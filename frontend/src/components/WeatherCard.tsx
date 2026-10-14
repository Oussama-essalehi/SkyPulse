import { clock } from "../lib/flight";
import { CATEGORY, skyText, useWeather, visibilityText, windText } from "../lib/weather";

/** Current weather of an airport, from its latest METAR. */
export function WeatherCard({ icao }: { icao: string }) {
  const { data, error } = useWeather(icao);
  if (error) return <p className="error small">Météo : {error}</p>;
  if (!data) return <p className="muted small">Chargement de la météo…</p>;
  if (!data.enabled) return <p className="muted small">Météo désactivée (WEATHER_LOOKUP=off).</p>;
  const w = data.weather;
  if (!w) return <p className="muted small">Pas de bulletin météo (METAR) pour cet aéroport.</p>;
  const category = w.flight_category ? CATEGORY[w.flight_category] : undefined;
  return (
    <section className="weather" aria-label={`Météo ${icao}`}>
      <header>
        <h3>Météo</h3>
        {w.observed_at && (
          <span className="muted small">observée à {clock(w.observed_at).utc}</span>
        )}
        {category && (
          <span
            className={`weather-category weather-${w.flight_category?.toLowerCase()}`}
            title={category.title}
          >
            {category.label}
          </span>
        )}
      </header>
      <div className="weather-main">
        <strong className="weather-temp">
          {w.temperature_c !== null ? `${Math.round(w.temperature_c)} °C` : "—"}
        </strong>
        <span>{w.conditions ?? w.sky ?? ""}</span>
      </div>
      <dl className="weather-facts">
        <div>
          <dt>Vent</dt>
          <dd>
            {w.wind_direction_deg !== null && !w.wind_variable && (
              <span
                className="wind-arrow"
                aria-hidden="true"
                style={{ transform: `rotate(${w.wind_direction_deg + 180}deg)` }}
              >
                ↑
              </span>
            )}
            {windText(w)}
          </dd>
        </div>
        <div>
          <dt>Visibilité</dt>
          <dd>{visibilityText(w)}</dd>
        </div>
        <div>
          <dt>Ciel</dt>
          <dd>{skyText(w)}</dd>
        </div>
        <div>
          <dt>Humidité</dt>
          <dd>{w.humidity_pct !== null ? `${w.humidity_pct} %` : "—"}</dd>
        </div>
        <div>
          <dt>Pression</dt>
          <dd>{w.pressure_hpa !== null ? `${w.pressure_hpa} hPa` : "—"}</dd>
        </div>
      </dl>
      {w.raw && (
        <details className="weather-raw">
          <summary>METAR brut</summary>
          <code>{w.raw}</code>
        </details>
      )}
    </section>
  );
}
