import { useCallback, useEffect, useRef, useState } from "react";
import { api, type IndexPredictionArtifact } from "@/lib/api";

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
  const [materialNewsCount, setMaterialNewsCount] = useState(0);
  const onUpdateRef = useRef(onUpdate);
  onUpdateRef.current = onUpdate;

  const refreshLight = useCallback(async () => {
    try {
      const res = await api.refreshIndexPrediction({ ticker, horizon_days: horizonDays });
      if (res.status === "ok") {
        setLastReason(res.reason || "scheduled_poll");
        onUpdateRef.current?.(res.artifact ?? null);
      } else {
        setLastReason(res.message || "refresh_failed");
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "refresh_failed";
      setLastReason(msg.length > 80 ? "refresh_failed" : msg);
    }
  }, [ticker, horizonDays]);

  const loadContext = useCallback(async () => {
    try {
      const ctx = await api.getPlanContext(ticker);
      setMaterialNewsCount(ctx.material_news_count ?? 0);
    } catch {
      /* ignore */
    }
  }, [ticker]);

  useEffect(() => {
    if (!enabled || pollMs <= 0 || pauseWhileRunning) {
      setCountdownSec(0);
      return;
    }

    void loadContext();
    void refreshLight();

    const pollId = window.setInterval(() => {
      void loadContext();
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
  }, [enabled, pollMs, pauseWhileRunning, refreshLight, loadContext]);

  return { lastReason, countdownSec, materialNewsCount, pausedForAnalysis: pauseWhileRunning };
}
