import { useEffect, useMemo, useState } from "react";
import { api, type TradePlanLiveContext, type TradePlanStaleness } from "@/lib/api";

const POLL_INTERVAL_MS = 60_000;

export interface LivePlanContextState {
  monitorEnabled: boolean;
  staleness: TradePlanStaleness | null;
  liveContext: TradePlanLiveContext | null;
  materialNewsCount: number;
  openPosition: boolean;
}

const DISABLED_STATE: LivePlanContextState = {
  monitorEnabled: false,
  staleness: null,
  liveContext: null,
  materialNewsCount: 0,
  openPosition: false,
};

export function useLivePlanContext(ticker: string | undefined): LivePlanContextState {
  const [payload, setPayload] = useState<Awaited<ReturnType<typeof api.getPlanContext>> | null>(
    null,
  );

  useEffect(() => {
    const key = (ticker || "").trim().toUpperCase();
    if (!key) {
      setPayload(null);
      return;
    }

    let cancelled = false;

    const poll = async () => {
      try {
        const response = await api.getPlanContext(key);
        if (!cancelled) setPayload(response);
      } catch {
        /* ignore transient poll errors */
      }
    };

    void poll();
    const intervalId = window.setInterval(poll, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [ticker]);

  return useMemo(() => {
    if (!payload?.monitor_enabled) return DISABLED_STATE;
    return {
      monitorEnabled: true,
      staleness: payload.staleness ?? null,
      liveContext: payload.live_context ?? null,
      materialNewsCount: payload.material_news_count ?? 0,
      openPosition: payload.open_position ?? false,
    };
  }, [payload]);
}
