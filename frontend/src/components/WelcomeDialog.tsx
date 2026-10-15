import { useEffect, useRef } from "react";

import { PALETTE, rgbCss } from "../lib/colors";
import { AlertKey } from "./AlertKey";

const SEEN_KEY = "skyradar.welcome.seen";

/** Whether this browser already saw the welcome dialog (storage may be unavailable). */
export function welcomeSeen(): boolean {
  try {
    return window.localStorage.getItem(SEEN_KEY) === "1";
  } catch {
    return false;
  }
}

function markSeen(): void {
  try {
    window.localStorage.setItem(SEEN_KEY, "1");
  } catch {
    /* private mode: the dialog simply shows again next time */
  }
}

interface Props {
  open: boolean;
  onClose: () => void;
}

/** First-visit explanation of the site, reopened with the "?" button of the top bar. */
export function WelcomeDialog({ open, onClose }: Props) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const close = () => {
    markSeen();
    onClose();
  };

  const low = rgbCss(PALETTE.stops[0][1]);
  const high = rgbCss(PALETTE.stops[PALETTE.stops.length - 1][1]);

  return (
    <dialog
      ref={ref}
      className="welcome"
      aria-labelledby="welcome-title"
      // Escape key (native "cancel"), or a click on the dimmed backdrop around the box.
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div className="welcome-body">
        {/* Focus starts on the title, so a phone shows the top of the text, not the button. */}
        <h2 id="welcome-title" tabIndex={-1} autoFocus>
          <span className="brand-dot" /> Bienvenue sur SkyRadar
        </h2>
        <p className="welcome-lead">
          Les avions du monde entier, en direct. Chaque icône est un avion réel, placé grâce au
          signal ADS-B qu'il émet et que capte le réseau collaboratif OpenSky.
        </p>

        <h3>Comment l'utiliser</h3>
        <ul className="welcome-list">
          <li>
            <strong>Cliquez sur un avion</strong> : compagnie, type d'appareil, départ et
            destination, altitude, vitesse et trajectoire.
          </li>
          <li>
            <strong>Cherchez</strong> un vol (AFR1234), un avion (code icao24) ou un aéroport (CDG,
            Orly…) dans la barre en haut.
          </li>
          <li>
            <strong>Cliquez sur un aéroport</strong> (les petits points foncés) : arrivées, départs,
            avions au sol et météo.
          </li>
          <li>
            <strong>Filtres</strong> : en vol ou au sol, altitude, pays d'immatriculation, aéroport.
          </li>
        </ul>

        <h3>Lire la carte</h3>
        <p>
          La couleur d'un avion indique son altitude : du{" "}
          <span className="swatch" style={{ background: low }} /> jaune près du sol au{" "}
          <span className="swatch" style={{ background: high }} /> turquoise au-dessus de 13 000 m
          (légende en bas à gauche). Les avions au sol sont gris clair.
        </p>
        <p>Les cercles signalent des alertes détectées automatiquement :</p>
        <AlertKey />
        <p className="muted small">
          La liste complète est dans <strong>Alertes</strong>, en haut à droite. Les positions
          arrivent à intervalles réguliers : entre deux mises à jour, chaque avion avance selon sa
          vitesse et son cap. Données ADS-B © OpenSky Network, usage non commercial.
        </p>

        <button className="welcome-start" onClick={close}>
          Explorer la carte
        </button>
      </div>
    </dialog>
  );
}
