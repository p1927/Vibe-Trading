import { useMemo } from "react";
import type { TradePlanLiveContext, TradePlanStaleness } from "@/lib/api";
import { usePlanContext } from "@/hooks/usePlanContext";

const POLL_INTERVAL_MS = 60_000;

export interface LivePlanContextState {
  monitorEnabled: boolean;
  staleness: TradePlanStaleness | null;
  liveContext: TradePlanLiveContext | null;
  /** The live read failed: staleness/liveContext are unknown, not "monitor off". */
  error: string | null;
}

export function useLivePlanContext(ticker: string | undefined): LivePlanContextState {
  const key = (ticker || "").trim().toUpperCase();
  const { context, error } = usePlanContext(key, { pollMs: POLL_INTERVAL_MS });
  return useMemo(() => {
    if (!context?.monitor_enabled) return { monitorEnabled: false, staleness: null, liveContext: null, error };
    return {
      monitorEnabled: true,
      staleness: context.staleness ?? null,
      liveContext: context.live_context ?? null,
      error: null,
    };
  }, [context, error]);
}
