import { useCallback, useEffect, useMemo, useState } from "react";
import { Database, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type CaptureRegistryResponse } from "@/lib/api";

const TIER_LABEL: Record<string, string> = {
  capture: "Capturing",
  scalar: "Scalar only",
  ephemeral: "Ephemeral",
};

const TIER_CLASS: Record<string, string> = {
  capture: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  scalar: "bg-muted text-muted-foreground",
  ephemeral: "bg-amber-500/15 text-amber-800 dark:text-amber-200",
};

export function DataCapturePanel() {
  const [data, setData] = useState<CaptureRegistryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const entity = useMemo(
    () => data?.registry?.entities?.find((e) => e.id === "NIFTY") ?? data?.registry?.entities?.[0],
    [data],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getCaptureRegistry("NIFTY");
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load capture registry");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleCapture = async () => {
    setBusy("toggle");
    setError(null);
    try {
      const res = await api.updateCaptureRegistry({
        entity_id: "NIFTY",
        patch: { capture_enabled: !entity?.capture_enabled },
      });
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    } finally {
      setBusy(null);
    }
  };

  const runBackfill = async () => {
    setBusy("backfill");
    setError(null);
    try {
      await api.runCaptureBackfill({ entity_id: "NIFTY", days: 365 });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Backfill failed");
    } finally {
      setBusy(null);
    }
  };

  const runIntraday = async () => {
    setBusy("intraday");
    setError(null);
    try {
      await api.runCaptureIntraday("NIFTY");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Intraday capture failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            <Database className="h-3 w-3" aria-hidden />
            Data capture (NIFTY 50)
          </p>
          <p className="mt-1 max-w-xl text-[11px] text-muted-foreground">
            Persist proprietary India market data (OpenAlgo chain, FII/DII, participant OI) into the hub.
            Easy-to-refetch series (yfinance macro) stay scalar-only.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] text-muted-foreground hover:bg-muted/50"
        >
          <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
          Refresh
        </button>
      </div>

      {error ? (
        <p className="mb-2 text-[11px] text-destructive">{error}</p>
      ) : null}

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={Boolean(entity?.capture_enabled)}
            disabled={busy !== null || loading}
            onChange={() => void toggleCapture()}
            className="h-4 w-4 rounded border"
          />
          Capture NIFTY proprietary data
        </label>
        <button
          type="button"
          disabled={busy !== null || loading}
          onClick={() => void runIntraday()}
          className="rounded-md border px-2 py-1 text-[10px] hover:bg-muted/50 disabled:opacity-50"
        >
          {busy === "intraday" ? "Capturing…" : "Capture chain now"}
        </button>
        <button
          type="button"
          disabled={busy !== null || loading}
          onClick={() => void runBackfill()}
          className="rounded-md border px-2 py-1 text-[10px] hover:bg-muted/50 disabled:opacity-50"
        >
          {busy === "backfill" ? "Backfilling…" : "Run backfill"}
        </button>
      </div>

      {data?.stats?.series ? (
        <div className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {Object.entries(data.stats.series).map(([name, row]) => (
            <div key={name} className="rounded-lg border bg-muted/20 px-3 py-2">
              <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">{name}</p>
              <p className="text-sm font-semibold tabular-nums">{row.rows ?? 0} rows</p>
              <p className="text-[10px] text-muted-foreground">
                {row.days ?? 0} days
                {row.last_capture_at ? ` · last ${new Date(row.last_capture_at).toLocaleString("en-IN")}` : ""}
              </p>
            </div>
          ))}
        </div>
      ) : null}

      {data?.factor_tree?.length ? (
        <details className="group">
          <summary className="cursor-pointer text-[11px] font-medium text-muted-foreground">
            Factor dependency tree ({data.factor_tree.reduce((n, g) => n + g.factors.length, 0)} factors)
          </summary>
          <div className="mt-2 max-h-64 space-y-3 overflow-y-auto pr-1">
            {data.factor_tree.map((group) => (
              <div key={group.category}>
                <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {group.category.replace(/_/g, " ")}
                </p>
                <ul className="mt-1 space-y-1">
                  {group.factors.map((f) => (
                    <li key={f.key} className="flex flex-wrap items-center gap-2 text-[11px]">
                      <span className="font-medium">{f.label ?? f.key}</span>
                      <span
                        className={cn(
                          "rounded px-1.5 py-0.5 text-[9px] font-medium uppercase",
                          TIER_CLASS[f.tier] ?? TIER_CLASS.scalar,
                        )}
                      >
                        {TIER_LABEL[f.tier] ?? f.tier}
                      </span>
                      <span className="text-muted-foreground">{f.source}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}
