import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type AutonomousAgentInstance,
  type WatchLiveSnapshot,
  type WatchRecord,
  type WatchRuleTelemetry,
} from "@/lib/api";

const REGISTRY_POLL_MS = 30_000;

interface Props {
  sessionId?: string | null;
  agentId?: string | null;
  pollMs: number;
  enabled?: boolean;
}

function resolveAgentPendingRules(agent: AutonomousAgentInstance): WatchLiveSnapshot | null {
  const raw =
    agent.watch_spec ??
    (agent.mandate_config?.watch_spec as AutonomousAgentInstance["watch_spec"] | undefined);
  const rules = raw?.rules;
  if (!Array.isArray(rules) || rules.length === 0) return null;

  const telemetryRules: WatchRuleTelemetry[] = rules
    .filter((row): row is Record<string, unknown> => typeof row === "object" && row !== null)
    .map((row) => {
      const symbol = String(row.symbol ?? "?");
      const metric = String(row.metric ?? "spot_move_pct");
      const threshold = Number(row.threshold ?? 0);
      const direction = String(row.direction ?? "either");
      let condition_text = `${symbol} ${metric} ${threshold}`;
      if (metric === "spot_move_pct") {
        condition_text = `${symbol} move ${direction} ≥${threshold}%`;
      } else if (metric === "level_above" || metric === "level_below") {
        condition_text = `${symbol} ${metric.replace(/_/g, " ")} ${threshold}`;
      }
      return {
        symbol,
        metric,
        condition_text,
        threshold,
        direction,
        current: {},
        distance: { fired: false, remaining: null, unit: metric.includes("level") ? "points" : "pct" },
        quote_available: false,
      };
    });

  return {
    watch_id: `agent:${agent.id}`,
    label: "strategy watch",
    rules: telemetryRules,
    last_fired_at: null,
  };
}

export function useWatchersLive({
  sessionId,
  agentId,
  pollMs,
  enabled = true,
}: Props) {
  const [liveWatches, setLiveWatches] = useState<WatchLiveSnapshot[]>([]);
  const [registryWatches, setRegistryWatches] = useState<WatchRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetchedAt, setFetchedAt] = useState<string | undefined>();
  const [marketOpen, setMarketOpen] = useState<boolean | undefined>();
  const [countdownSec, setCountdownSec] = useState(0);
  const [pendingSnapshot, setPendingSnapshot] = useState<WatchLiveSnapshot | null>(null);

  const liveInFlightRef = useRef(false);
  const registryInFlightRef = useRef(false);

  const loadRegistry = useCallback(async () => {
    if (!sessionId && !agentId) {
      setRegistryWatches([]);
      return;
    }
    if (registryInFlightRef.current) return;
    registryInFlightRef.current = true;
    try {
      const res = await api.listWatches({
        sessionId: agentId ? undefined : (sessionId ?? undefined),
        agentId: agentId ?? undefined,
      });
      setRegistryWatches(res.watches ?? []);
    } catch {
      /* registry list is best-effort */
    } finally {
      registryInFlightRef.current = false;
    }
  }, [sessionId, agentId]);

  const loadLive = useCallback(async () => {
    if (!sessionId && !agentId) {
      setLiveWatches([]);
      setPendingSnapshot(null);
      return;
    }
    if (liveInFlightRef.current) return;
    liveInFlightRef.current = true;
    setLoading(true);
    try {
      const res = await api.getWatchesLive({
        sessionId: agentId ? undefined : (sessionId ?? undefined),
        agentId: agentId ?? undefined,
      });
      const rows = res.watches ?? [];
      if (rows.length === 0 && agentId) {
        try {
          const agent = await api.getAutonomousAgent(agentId);
          setPendingSnapshot(resolveAgentPendingRules(agent));
        } catch {
          setPendingSnapshot(null);
        }
      } else {
        setPendingSnapshot(null);
      }
      setLiveWatches(rows);
      setFetchedAt(res.fetched_at);
      setMarketOpen(res.market_open ?? undefined);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load live watches");
    } finally {
      setLoading(false);
      liveInFlightRef.current = false;
    }
  }, [sessionId, agentId]);

  const refresh = useCallback(async () => {
    await Promise.all([loadRegistry(), loadLive()]);
  }, [loadRegistry, loadLive]);

  useEffect(() => {
    if (!enabled || (!sessionId && !agentId)) return;
    void loadRegistry();
    const id = window.setInterval(() => void loadRegistry(), REGISTRY_POLL_MS);
    return () => window.clearInterval(id);
  }, [enabled, sessionId, agentId, loadRegistry]);

  useEffect(() => {
    if (!enabled || (!sessionId && !agentId)) {
      setCountdownSec(0);
      return;
    }

    void loadLive();

    if (pollMs <= 0) {
      setCountdownSec(0);
      return;
    }

    const pollId = window.setInterval(() => void loadLive(), pollMs);
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
  }, [enabled, sessionId, agentId, pollMs, loadLive]);

  const watches =
    liveWatches.length > 0 ? liveWatches : pendingSnapshot ? [pendingSnapshot] : [];

  return {
    watches,
    registryWatches,
    loading,
    error,
    fetchedAt,
    marketOpen,
    countdownSec,
    refresh,
    liveEnabled: pollMs > 0,
  };
}
