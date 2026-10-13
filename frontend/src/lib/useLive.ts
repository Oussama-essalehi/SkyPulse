import { useEffect, useState } from "react";

import { liveSocketUrl } from "./api";
import { AircraftStore } from "./store";
import type { Alert, LiveMessage } from "./types";

export type ConnectionStatus = "connecting" | "open" | "closed";

export interface LiveFeed {
  store: AircraftStore;
  status: ConnectionStatus;
  alerts: Alert[];
  /** Bumped on every applied message (drives light-weight React updates). */
  tick: number;
}

/** WebSocket connection with exponential reconnect, feeding a mutable store. */
export function useLive(): LiveFeed {
  const [store] = useState(() => new AircraftStore());
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry = 0;
    let timer: number | undefined;
    let disposed = false;

    const connect = () => {
      setStatus("connecting");
      socket = new WebSocket(liveSocketUrl());
      socket.onopen = () => {
        retry = 0;
        setStatus("open");
      };
      socket.onmessage = (event: MessageEvent<string>) => {
        const message = JSON.parse(event.data) as LiveMessage;
        if (message.type === "alerts") {
          setAlerts(message.alerts);
        } else {
          store.apply(message);
          setTick((t) => t + 1);
        }
      };
      socket.onclose = () => {
        if (disposed) return;
        setStatus("closed");
        const delay = Math.min(30_000, 1000 * 2 ** retry++);
        timer = window.setTimeout(connect, delay);
      };
      socket.onerror = () => socket?.close();
    };
    connect();
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      socket?.close();
    };
  }, [store]);

  return { store, status, alerts, tick };
}
