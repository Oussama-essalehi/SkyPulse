import type { Filters } from "./types";

export const ALERT_LABELS: Record<string, string> = {
  squawk_7500: "Intervention illicite (7500)",
  squawk_7600: "Panne radio (7600)",
  squawk_7700: "Urgence (7700)",
  rapid_descent: "Descente rapide",
  holding_pattern: "Attente / circuit",
  signal_loss: "Perte de signal",
  diversion: "Déroutement",
  trajectory_outlier: "Trajectoire atypique",
};

export const DEFAULT_FILTERS: Filters = {
  minAlt: 0,
  maxAlt: 15000,
  country: "",
  status: "all",
  airport: null,
  aircraft: null,
};
