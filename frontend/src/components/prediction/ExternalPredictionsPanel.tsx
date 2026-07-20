import { useCallback, useState } from "react";
import { Loader2, Plus, RefreshCw, Search, Trash2 } from "lucide-react";
import { ExternalPredictionCard } from "@/components/prediction/ExternalPredictionCard";
import { ExternalPredictionsComparisonChart } from "@/components/charts/ExternalPredictionsComparisonChart";
import { ExternalPredictionsRefreshLogPanel } from "@/components/prediction/ExternalPredictionsRefreshLogPanel";
import { HORIZON_OPTIONS } from "@/components/prediction/PredictionControls";
import type { ExternalRefreshPhase } from "@/hooks/useExternalPredictions";
import type { ExternalPredictionSnapshot, PipelineLogEntry } from "@/lib/api";
import { filterVisiblePredictions } from "@/lib/externalPredictionsUtils";
import { cn } from "@/lib/utils";

function fmtTimestamp(iso: string | undefined): string {
  if (!iso) return "Never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

interface DiscoverPanelProps {
  onDiscover: () => Promise<void>;
  onAddSource: (candidate: Record<string, unknown>) => Promise<void>;
  onRemoveSource: (sourceId: string) => Promise<void>;
  candidates: Array<Record<string, unknown>>;
  sources: ExternalPredictionSnapshot["sources"];
  discovering: boolean;
  busySourceId: string | null;
}

function ExternalSourceDiscoverPanel({
  onDiscover,
  onAddSource,
  onRemoveSource,
  candidates,
  sources = [],
  discovering,
  busySourceId,
}: DiscoverPanelProps) {
  const watchlisted = sources?.filter((s) => s.watchlisted) ?? [];
  const discovered = sources?.filter((s) => !s.watchlisted) ?? [];

  return (
    <section className="rounded-xl border border-border/60 bg-card/30 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">Sources & discovery</h2>
          <p className="text-[11px] text-muted-foreground">
            Crawl4AI fetches landing pages and linked articles for the selected horizon. MiniMax extracts targets and rationale.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void onDiscover()}
          disabled={discovering}
          className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 bg-background px-3 py-1.5 text-[11px] font-semibold hover:bg-muted/40 disabled:opacity-50"
        >
          {discovering ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
          Discover sources
        </button>
      </div>

      {candidates.length ? (
        <div className="mt-3 space-y-2">
          <p className="text-[11px] font-medium text-muted-foreground">New candidates</p>
          {candidates.map((row) => {
            const domain = String(row.domain ?? "");
            const key = domain || String(row.display_name ?? "");
            return (
              <div
                key={key}
                className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-border/50 bg-background/60 p-2.5"
              >
                <div className="min-w-0">
                  <p className="text-[12px] font-medium">{String(row.display_name ?? domain)}</p>
                  <p className="text-[10px] text-muted-foreground">{domain}</p>
                  {row.snippet ? (
                    <p className="mt-1 line-clamp-2 text-[11px] text-foreground/80">{String(row.snippet)}</p>
                  ) : null}
                </div>
                <button
                  type="button"
                  onClick={() => void onAddSource(row)}
                  disabled={busySourceId === key}
                  className="inline-flex shrink-0 items-center gap-1 rounded-md bg-primary px-2 py-1 text-[10px] font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  {busySourceId === key ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Plus className="h-3 w-3" />
                  )}
                  Add to watchlist
                </button>
              </div>
            );
          })}
        </div>
      ) : null}

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <div>
          <p className="mb-2 text-[11px] font-medium text-muted-foreground">Watchlist ({watchlisted.length})</p>
          <ul className="space-y-1">
            {watchlisted.map((src) => (
              <li
                key={src.id}
                className="flex items-center justify-between rounded-md bg-muted/20 px-2 py-1.5 text-[11px]"
              >
                <span>{src.display_name}</span>
                {src.removable ? (
                  <button
                    type="button"
                    onClick={() => void onRemoveSource(src.id)}
                    disabled={busySourceId === src.id}
                    className="text-muted-foreground hover:text-red-600"
                    title="Remove from watchlist"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
        {discovered.length ? (
          <div>
            <p className="mb-2 text-[11px] font-medium text-muted-foreground">Discovered (not watchlisted)</p>
            <ul className="space-y-1">
              {discovered.map((src) => (
                <li
                  key={src.id}
                  className="flex items-center justify-between rounded-md bg-muted/10 px-2 py-1.5 text-[11px]"
                >
                  <span>{src.display_name}</span>
                  <button
                    type="button"
                    onClick={() =>
                      void onAddSource({
                        display_name: src.display_name,
                        domain: src.domains?.[0],
                        id: src.id,
                      })
                    }
                    disabled={busySourceId === src.id}
                    className="inline-flex items-center gap-1 text-primary hover:underline"
                  >
                    <Plus className="h-3 w-3" />
                    Add
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </section>
  );
}

interface Props {
  snapshot: ExternalPredictionSnapshot | null;
  loading: boolean;
  refreshing: boolean;
  refreshPhase?: ExternalRefreshPhase;
  refreshLogs: PipelineLogEntry[];
  runJobId?: string | null;
  reattached?: boolean;
  error: string | null;
  horizonDays: number;
  priceSeries?: Array<{ date?: string; close?: number | null }>;
  priceLoading?: boolean;
  onHorizonChange: (days: number) => void;
  onRefresh: () => Promise<void>;
  onDiscover: () => Promise<Array<Record<string, unknown>>>;
  onAddSource: (candidate: Record<string, unknown>) => Promise<void>;
  onRemoveSource: (sourceId: string) => Promise<void>;
}

export function ExternalPredictionsPanel({
  snapshot,
  loading,
  refreshing,
  refreshPhase = "idle",
  refreshLogs,
  runJobId,
  reattached,
  error,
  horizonDays,
  priceSeries,
  priceLoading,
  onHorizonChange,
  onRefresh,
  onDiscover,
  onAddSource,
  onRemoveSource,
}: Props) {
  const [discovering, setDiscovering] = useState(false);
  const [candidates, setCandidates] = useState<Array<Record<string, unknown>>>([]);
  const [busySourceId, setBusySourceId] = useState<string | null>(null);

  const handleDiscover = useCallback(async () => {
    setDiscovering(true);
    try {
      const rows = await onDiscover();
      setCandidates(rows);
    } finally {
      setDiscovering(false);
    }
  }, [onDiscover]);

  const handleAdd = useCallback(
    async (candidate: Record<string, unknown>) => {
      const key = String(candidate.domain ?? candidate.display_name ?? "");
      setBusySourceId(key);
      try {
        await onAddSource(candidate);
      } finally {
        setBusySourceId(null);
      }
    },
    [onAddSource],
  );

  const handleRemove = useCallback(
    async (sourceId: string) => {
      setBusySourceId(sourceId);
      try {
        await onRemoveSource(sourceId);
      } finally {
        setBusySourceId(null);
      }
    },
    [onRemoveSource],
  );

  const sourceMap = new Map((snapshot?.sources ?? []).map((s) => [s.id, s]));
  const allPredictions = snapshot?.predictions ?? [];
  const visiblePredictions = filterVisiblePredictions(allPredictions);
  const skippedCount = allPredictions.length - visiblePredictions.length;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border/60 bg-card/40 p-3">
        <div>
          <h1 className="text-base font-semibold">Miscellaneous — Street views</h1>
          <p className="text-[11px] text-muted-foreground">
            Third-party NIFTY 50 forecasts from media, brokers, and global banks.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2 text-[11px] text-muted-foreground">
            Horizon
            <select
              value={horizonDays}
              onChange={(e) => onHorizonChange(Number(e.target.value))}
              className="rounded-md border border-border/60 bg-background px-2 py-1 text-[11px] text-foreground"
            >
              {[7, 14, 21, 30, 60].map((d) => {
                const opt = HORIZON_OPTIONS.find((o) => o.days === d);
                return (
                  <option key={d} value={d}>
                    {opt?.label ?? `${d}d`}
                  </option>
                );
              })}
            </select>
          </label>
          <button
            type="button"
            onClick={() => void onRefresh()}
            disabled={refreshing}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[11px] font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50",
            )}
          >
            {refreshing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            {refreshPhase === "starting"
              ? "Starting…"
              : refreshing
                ? "Refreshing…"
                : "Refresh"}
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
        <span>Last updated: {fmtTimestamp(snapshot?.fetched_at)}</span>
        {refreshing && runJobId ? (
          <span className="rounded-full bg-blue-500/15 px-2 py-0.5 font-mono text-[10px] text-blue-700 dark:text-blue-300">
            Run {runJobId.slice(0, 8)}
            {reattached ? " · reattached" : ""}
          </span>
        ) : null}
        {snapshot?.is_stale ? (
          <span className="rounded-full bg-amber-500/15 px-2 py-0.5 font-semibold text-amber-700 dark:text-amber-300">
            Stale — click Refresh
          </span>
        ) : snapshot?.fetched_at ? (
          <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 font-semibold text-emerald-700 dark:text-emerald-300">
            Cached
          </span>
        ) : null}
      </div>

      {error ? (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-[12px] text-red-700 dark:text-red-300">
          {error}
        </div>
      ) : null}

      <ExternalPredictionsRefreshLogPanel
        logs={refreshLogs}
        refreshing={refreshing}
        refreshPhase={refreshPhase}
      />

      {loading && !snapshot ? (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading cached forecasts…
        </div>
      ) : null}

      {snapshot ? (
        <>
          <ExternalPredictionsComparisonChart snapshot={snapshot} />
          {visiblePredictions.length === 0 && !refreshing ? (
            <div className="rounded-lg border border-border/60 bg-muted/20 px-4 py-8 text-center text-[12px] text-muted-foreground">
              No NIFTY 50 index forecasts found for {horizonDays}d — try another horizon or click Refresh.
              {skippedCount > 0 ? (
                <p className="mt-2 text-[11px]">{skippedCount} source(s) skipped (no index forecast for this horizon).</p>
              ) : null}
            </div>
          ) : null}
          {skippedCount > 0 && visiblePredictions.length > 0 ? (
            <p className="text-[11px] text-muted-foreground">
              {skippedCount} source(s) hidden — no usable NIFTY 50 index forecast for {horizonDays}d.
            </p>
          ) : null}
          <ExternalSourceDiscoverPanel
            onDiscover={handleDiscover}
            onAddSource={handleAdd}
            onRemoveSource={handleRemove}
            candidates={candidates}
            sources={snapshot.sources}
            discovering={discovering}
            busySourceId={busySourceId}
          />
          <div className="grid gap-4">
            {visiblePredictions.map((record) => (
              <ExternalPredictionCard
                key={record.source_id}
                record={record}
                source={sourceMap.get(record.source_id)}
                horizonDays={horizonDays}
                priceSeries={priceSeries}
                priceLoading={priceLoading}
              />
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}
