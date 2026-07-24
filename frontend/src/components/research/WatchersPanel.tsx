import { Eye, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { WatchRuleTelemetryRow } from "@/components/research/WatchRuleTelemetryRow";
import { WatchersPollControls } from "@/components/research/WatchersPollControls";
import { usePollIntervalPreference } from "@/hooks/usePollIntervalPreference";
import { useWatchersLive } from "@/hooks/useWatchersLive";
import { api } from "@/lib/api";
import {
  WATCHERS_DEFAULT_POLL_MS,
  WATCHERS_POLL_STORAGE_KEY,
} from "@/lib/pollIntervalOptions";
import { cn } from "@/lib/utils";

interface Props {
  sessionId?: string | null;
  agentId?: string | null;
  className?: string;
}

export function WatchersPanel({ sessionId, agentId, className }: Props) {
  const { pollMs, setPollMs } = usePollIntervalPreference(
    WATCHERS_POLL_STORAGE_KEY,
    WATCHERS_DEFAULT_POLL_MS,
  );
  const {
    watches,
    loading,
    error,
    fetchedAt,
    marketOpen,
    countdownSec,
    refresh,
    liveEnabled,
  } = useWatchersLive({ sessionId, agentId, pollMs, enabled: Boolean(sessionId || agentId) });

  const [deletingId, setDeletingId] = useState<string | null>(null);

  const onDelete = async (watchId: string) => {
    if (watchId.startsWith("agent:")) {
      toast.message("Watch activates with the agent — edit rules in chat.");
      return;
    }
    setDeletingId(watchId);
    try {
      await api.deleteWatch(watchId);
      toast.success("Watch removed");
      await refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete watch");
    } finally {
      setDeletingId(null);
    }
  };

  if (!sessionId && !agentId) {
    return (
      <p className={cn("text-[11px] text-muted-foreground", className)}>
        No session bound — watches appear when the agent creates them.
      </p>
    );
  }

  return (
    <div className={cn("space-y-3", className)}>
      <WatchersPollControls
        pollMs={pollMs}
        onPollChange={setPollMs}
        countdownSec={countdownSec}
        liveEnabled={liveEnabled}
        fetchedAt={fetchedAt}
      />

      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-xs font-semibold">
          <Eye className="h-3.5 w-3.5 text-muted-foreground" />
          Active watches
          {marketOpen === false && (
            <span className="text-[10px] font-normal text-muted-foreground">· market closed</span>
          )}
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          className="text-[10px] text-muted-foreground hover:text-foreground"
        >
          Refresh
        </button>
      </div>

      {error && (
        <p className="text-[11px] text-destructive">{error}</p>
      )}

      {loading && watches.length === 0 && (
        <p className="text-[11px] text-muted-foreground">Loading watches…</p>
      )}

      {!loading && watches.length === 0 && (
        <p className="text-[11px] text-muted-foreground">
          No active watches. Ask the agent to watch a symbol or set a strategy watch rule.
        </p>
      )}

      {watches.map((watch) => {
        const pendingOnly = watch.watch_id.startsWith("agent:");
        return (
          <div
            key={watch.watch_id}
            className="rounded-md border bg-muted/30 px-2.5 py-2 text-[11px]"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1 space-y-1.5">
                <div className="truncate font-medium">{watch.label || watch.watch_id}</div>
                {pendingOnly && (
                  <div className="text-[10px] text-muted-foreground">
                    On agent record — Nautilus registry syncs after plan approval or bootstrap completes.
                  </div>
                )}
                {watch.last_fired_at && (
                  <div className="text-[10px] text-amber-600 dark:text-amber-400">
                    Last fired {new Date(watch.last_fired_at).toLocaleString()}
                  </div>
                )}
                {watch.rules.map((rule, idx) => (
                  <WatchRuleTelemetryRow key={`${watch.watch_id}-${rule.symbol}-${rule.metric}-${idx}`} rule={rule} />
                ))}
              </div>
              {!pendingOnly && (
                <button
                  type="button"
                  title="Remove watch"
                  disabled={deletingId === watch.watch_id}
                  onClick={() => void onDelete(watch.watch_id)}
                  className="shrink-0 rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
