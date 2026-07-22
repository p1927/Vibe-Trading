import { Eye, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { api, type WatchRecord } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  sessionId?: string | null;
  agentId?: string | null;
  className?: string;
}

export function WatchersPanel({ sessionId, agentId, className }: Props) {
  const [watches, setWatches] = useState<WatchRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!sessionId && !agentId) {
      setWatches([]);
      return;
    }
    setLoading(true);
    try {
      const res = await api.listWatches({
        sessionId: sessionId ?? undefined,
        agentId: agentId ?? undefined,
      });
      setWatches(res.watches ?? []);
    } catch {
      setWatches([]);
    } finally {
      setLoading(false);
    }
  }, [sessionId, agentId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const onDelete = async (watchId: string) => {
    setDeletingId(watchId);
    try {
      await api.deleteWatch(watchId);
      toast.success("Watch removed");
      await load();
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
    <div className={cn("space-y-2", className)}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-xs font-semibold">
          <Eye className="h-3.5 w-3.5 text-muted-foreground" />
          Active watches
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="text-[10px] text-muted-foreground hover:text-foreground"
        >
          Refresh
        </button>
      </div>

      {loading && watches.length === 0 && (
        <p className="text-[11px] text-muted-foreground">Loading watches…</p>
      )}

      {!loading && watches.length === 0 && (
        <p className="text-[11px] text-muted-foreground">
          No active watches. Ask the agent to watch a symbol or set a strategy watch rule.
        </p>
      )}

      {watches.map((watch) => {
        const rules = (watch.watch_spec?.rules as Array<{ symbol?: string; metric?: string; threshold?: number }>) ?? [];
        const ruleSummary =
          rules.length > 0
            ? rules
                .slice(0, 3)
                .map((r) => `${r.symbol ?? "?"} ${r.metric ?? "rule"} ${r.threshold ?? ""}`.trim())
                .join(" · ")
            : (watch.symbols ?? []).join(", ") || "watch";
        return (
          <div
            key={watch.watch_id}
            className="rounded-md border bg-muted/30 px-2.5 py-2 text-[11px]"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate font-medium">{watch.label || ruleSummary}</div>
                <div className="mt-0.5 truncate text-muted-foreground">{ruleSummary}</div>
                {watch.last_fired_at && (
                  <div className="mt-1 text-[10px] text-amber-600 dark:text-amber-400">
                    Last fired {new Date(watch.last_fired_at).toLocaleString()}
                  </div>
                )}
              </div>
              <button
                type="button"
                title="Remove watch"
                disabled={deletingId === watch.watch_id}
                onClick={() => void onDelete(watch.watch_id)}
                className="shrink-0 rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
