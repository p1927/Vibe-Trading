import { useCallback, useEffect, useRef, useState } from "react";
import { api, type IndexPredictionArtifact } from "@/lib/api";
import { finiteOrNull } from "@/lib/finite";
import { usePlanContext } from "@/hooks/usePlanContext";

interface Props {
  ticker?: string;
  horizonDays?: number;
  pollMs: number;
  enabled?: boolean;
  pauseWhileRunning?: boolean;
  onUpdate?: (artifact: IndexPredictionArtifact | null) => void;
}

export function useIndexPredictionLive({
  ticker = "NIFTY",
  horizonDays = 14,
  pollMs,
  enabled = true,
  pauseWhileRunning = false,
  onUpdate,
}: Props) {
  const [lastReason, setLastReason] = useState<string | null>(null);
  const [countdownSec, setCountdownSec] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  // null = unknown (not read yet, the read failed, or it carried no count): never shown as "0 material headlines".
  const { context } = usePlanContext(ticker, {
    pollMs,
    enabled: enabled && pollMs > 0 && !pauseWhileRunning,
  });
  const materialNewsCount = finiteOrNull(context?.material_news_count);
  const onUpdateRef = useRef(onUpdate);
  onUpdateRef.current = onUpdate;
  const refreshInFlightRef = useRef(false);

  const refreshLight = useCallback(async () => {
    if (refreshInFlightRef.current) {
      return;
    }
    refreshInFlightRef.current = true;
    setRefreshing(true);
    try {
      // The server runs a poll only in market hours, never during an armed replay, one at a time.
      // Ask its gate first so a closed market / armed or unknown replay never sends the POST at all.
      const gate = await api.getIndexPredictionRefreshGate(ticker);
      if (gate.skip_reason) {
        setLastReason(`skipped: ${gate.skip_reason}`);
        return;
      }
      const res = await api.refreshIndexPrediction({ ticker, horizon_days: horizonDays });
      if (res.status === "skipped") {
        setLastReason(`skipped: ${res.reason || "unknown"}`);
      } else if (res.status === "ok") {
        setLastReason(res.reason || "scheduled_poll");
        onUpdateRef.current?.(res.artifact ?? null);
      } else {
        setLastReason(res.message || "refresh_failed");
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "refresh_failed";
      setLastReason(msg.length > 80 ? "refresh_failed" : msg);
    } finally {
      refreshInFlightRef.current = false;
      setRefreshing(false);
    }
  }, [ticker, horizonDays]);

  useEffect(() => {
    if (!enabled || pollMs <= 0 || pauseWhileRunning) {
      setCountdownSec(0);
      return;
    }

    void refreshLight();

    const pollId = window.setInterval(() => {
      void refreshLight();
    }, pollMs);

    let remaining = Math.floor(pollMs / 1000);
    setCountdownSec(remaining);
    const tickId = window.setInterval(() => {
      remaining -= 1;
      if (remaining <= 0) remaining = Math.floor(pollMs / 1000);
      setCountdownSec(remaining);
    }, 1000);

    return () => {
      window.clearInterval(pollId);
      window.clearInterval(tickId);
    };
  }, [enabled, pollMs, pauseWhileRunning, refreshLight]);

  return { lastReason, countdownSec, materialNewsCount, refreshing, pausedForAnalysis: pauseWhileRunning };
}
