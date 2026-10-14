import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type LiveHalted, type LiveStatus } from "@/lib/api";

/** Poll cadence for the shared `GET /live/status` snapshot. */
export const LIVE_STATUS_POLL_INTERVAL_MS = 15_000;

export function normalizeBrokerScope(broker: string | null | undefined): string | null {
  const normalized = broker?.trim().toLowerCase();
  return normalized || null;
}

export function isGlobalLiveHalt(halt: LiveHalted | null): boolean {
  return halt != null && normalizeBrokerScope(halt.broker) == null;
}

export function haltScopeStillActive(halt: LiveHalted, status: LiveStatus): boolean {
  const broker = normalizeBrokerScope(halt.broker);
  if (!broker) return status.global_halted;
  return status.global_halted || status.brokers.some((item) => (
    normalizeBrokerScope(item.auth.broker) === broker && item.halted
  ));
}

interface Options {
  /** Bump to force an immediate re-poll on a live event (commit / halt / resume). */
  refreshKey?: number;
  /** Runs on every successful snapshot (the page clears a halt banner whose scope is no longer halted). */
  onSnapshot?: (next: LiveStatus) => void;
  /** Refreshed on the same tick and by the same `refresh()` (the Agent page's connector list). */
  alongside?: () => Promise<unknown>;
}

/**
 * The one poller of `GET /live/status` (D306: `pages/Agent.tsx` and `LiveRuntimePanel` each had a copy).
 * A 404/501 means the backend does not serve it (panel hides); any other error keeps the last snapshot but
 * sets `error`, so a halted/running state read minutes ago does not look current. A response that arrives
 * after a newer request was issued is dropped (rapid refresh bumps would otherwise overwrite newer state).
 */
export function useLiveStatus({ refreshKey = 0, onSnapshot, alongside }: Options = {}) {
  const [liveStatus, setLiveStatus] = useState<LiveStatus | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seqRef = useRef(0);
  const onSnapshotRef = useRef(onSnapshot);
  const alongsideRef = useRef(alongside);
  onSnapshotRef.current = onSnapshot;
  alongsideRef.current = alongside;

  const fetchStatus = useCallback(async () => {
    const seq = ++seqRef.current;
    try {
      const next = await api.getLiveStatus();
      if (seq !== seqRef.current) return;
      setLiveStatus(next);
      onSnapshotRef.current?.(next);
      setUnavailable(false);
      setError(null);
    } catch (caught) {
      if (seq !== seqRef.current) return;
      if (caught instanceof ApiError && (caught.status === 404 || caught.status === 501)) {
        setLiveStatus(null);
        setUnavailable(true);
      } else {
        setError(caught instanceof Error ? caught.message : "request failed");
      }
    }
  }, []);

  const refresh = useCallback(async () => {
    await Promise.all([fetchStatus(), alongsideRef.current?.()]);
  }, [fetchStatus]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), LIVE_STATUS_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (refreshKey > 0) void refresh();
  }, [refreshKey, refresh]);

  return { liveStatus, unavailable, error, refresh };
}
