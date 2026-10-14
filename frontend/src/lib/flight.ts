import type { AirportInfo, FlightInfo } from "./types";

export function duration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds / 60));
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h > 0 ? `${h} h ${String(m).padStart(2, "0")}` : `${m} min`;
}

/** "14:05 UTC" and the viewer's local time ("16:05", null when the viewer is on UTC). */
export function clock(epoch: number, timeZone?: string): { utc: string; local: string | null } {
  const date = new Date(epoch * 1000);
  const utc = date.toISOString().slice(11, 16);
  const local = date.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone });
  return { utc: `${utc} UTC`, local: local === utc ? null : local };
}

/** "Paris (Roissy-en-France, Val-d'Oise)" -> "Paris": OurAirports details the commune. */
export function placeName(city: string | null | undefined): string | null {
  return city ? city.replace(/\s*\(.*\)\s*$/, "") || city : null;
}

/** Short code (IATA when known, as on boarding passes) and the place name. */
export function airportLabel(airport: AirportInfo | null): { code: string; place: string } {
  if (!airport) return { code: "?", place: "Inconnu" };
  const place = placeName(airport.city) ?? airport.name ?? airport.icao;
  return { code: airport.iata ?? airport.icao, place };
}

/** One sentence per source, so the user knows how much to trust each piece. */
export function sourceNotes(flight: FlightInfo): string[] {
  const notes: string[] = [];
  if (flight.departure_source === "reconstructed") notes.push("Décollage observé par SkyRadar.");
  if (flight.arrival_source === "route_db" || flight.departure_source === "route_db")
    notes.push(
      "Route publiée pour ce callsign (base adsbdb), cohérente avec la position et le cap.",
    );
  if (flight.arrival_source === "history") {
    const stats =
      flight.route_flights !== null
        ? ` (${flight.route_flights} vols, ${Math.round((flight.route_share ?? 0) * 100)} %)`
        : "";
    notes.push(`Destination habituelle de ce callsign dans l'historique${stats}.`);
  }
  if (flight.departure_time_estimated)
    notes.push("Heure de départ estimée d'après la distance déjà parcourue.");
  if (flight.rejected_route)
    notes.push(`Route publiée ${flight.rejected_route} ignorée : incohérente avec la position.`);
  return notes;
}
