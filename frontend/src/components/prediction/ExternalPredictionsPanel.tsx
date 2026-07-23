import { useCallback, useState } from "react";
import { Loader2, Plus, RefreshCw, Search, Trash2 } from "lucide-react";
import { ExternalPredictionCard } from "@/components/prediction/ExternalPredictionCard";
import { ExternalPredictionsComparisonChart } from "@/components/charts/ExternalPredictionsComparisonChart";
import { ExternalPredictionsRefreshLogPanel } from "@/components/prediction/ExternalPredictionsRefreshLogPanel";
import { HORIZON_OPTIONS } from "@/components/prediction/PredictionControls";
import type { ExternalRefreshPhase } from "@/hooks/useExternalPredictions";
import type { ExternalPredictionSnapshot, PipelineLogEntry } from "@/lib/api";
import { filterVisiblePredictions, computeStreetSummary, validateAddSourceRequest, buildAddSourcePayload, candidateNeedsEntryUrls, parseMultilineList, normalizeDomain, hasHorizonMismatch } from "@/lib/externalPredictionsUtils";
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
  onRequestAdd: (candidate: Record<string, unknown>) => void;
}

function parseLines(text: string): string[] {
  return parseMultilineList(text);
}

interface EntryUrlsPromptProps {
  candidate: Record<string, unknown>;
  onCancel: () => void;
  onSubmit: (entryUrls: string[]) => Promise<void>;
  submitting: boolean;
  error: string | null;
}

function EntryUrlsPrompt({ candidate, onCancel, onSubmit, submitting, error }: EntryUrlsPromptProps) {
  const domain =
    normalizeDomain(String(candidate.domain ?? "")) ||
    normalizeDomain(String((candidate.domains as string[] | undefined)?.[0] ?? ""));
  const sampleUrl = String(candidate.sample_url ?? "").trim();
  const [entryUrlsText, setEntryUrlsText] = useState(sampleUrl);
  const [localError, setLocalError] = useState<string | null>(null);

  const handleSubmit = useCallback(async () => {
    const entry_urls = parseLines(entryUrlsText);
    const validated = buildAddSourcePayload(candidate, entry_urls);
    if (!validated.ok) {
      setLocalError(validated.error ?? "Invalid entry URLs.");
      return;
    }
    setLocalError(null);
    await onSubmit(entry_urls);
  }, [candidate, entryUrlsText, onSubmit]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-xl border border-border/60 bg-card p-4 shadow-lg">
        <h3 className="text-sm font-semibold">Entry URLs required</h3>
        <p className="mt-1 text-[11px] text-muted-foreground">
          Add at least one landing page URL for{" "}
          <span className="font-medium text-foreground">
            {String(candidate.display_name ?? domain ?? "this source")}
          </span>
          {domain ? ` (${domain})` : ""} so the browse agent can reach forecast pages.
        </p>
        <textarea
          value={entryUrlsText}
          onChange={(e) => setEntryUrlsText(e.target.value)}
          rows={4}
          className="mt-3 w-full rounded-md border border-border/60 bg-background px-2 py-1.5 text-[11px]"
          placeholder={domain ? `https://${domain}/markets\nhttps://${domain}/markets/{horizon}d` : "https://example.com/markets"}
        />
        {localError || error ? (
          <p className="mt-2 text-[10px] text-red-600 dark:text-red-400">{localError ?? error}</p>
        ) : null}
        <div className="mt-3 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="rounded-md border border-border/60 px-3 py-1.5 text-[11px] font-medium hover:bg-muted/40 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void handleSubmit()}
            disabled={submitting}
            className="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-[11px] font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {submitting ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
            Add to watchlist
          </button>
        </div>
      </div>
    </div>
  );
}

function ExternalSourceDiscoverPanel({
  onDiscover,
  onAddSource,
  onRemoveSource,
  candidates,
  sources = [],
  discovering,
  busySourceId,
  onRequestAdd,
}: DiscoverPanelProps) {
  const watchlisted = sources?.filter((s) => s.watchlisted) ?? [];
  const discovered = sources?.filter((s) => !s.watchlisted) ?? [];
  const [manualName, setManualName] = useState("");
  const [manualDomain, setManualDomain] = useState("");
  const [manualEntryUrls, setManualEntryUrls] = useState("");
  const [manualError, setManualError] = useState<string | null>(null);
  const [addingManual, setAddingManual] = useState(false);

  const submitManualAdd = useCallback(async () => {
    const validated = validateAddSourceRequest({
      displayName: manualName,
      domains: [manualDomain],
      entryUrls: parseLines(manualEntryUrls),
      kind: "media",
    });
    if (!validated.ok) {
      setManualError(validated.error ?? "Invalid add-site request.");
      return;
    }
    setManualError(null);
    setAddingManual(true);
    try {
      await onAddSource(validated.payload!);
      setManualName("");
      setManualDomain("");
      setManualEntryUrls("");
    } catch (err) {
      setManualError(err instanceof Error ? err.message : "Failed to add source.");
    } finally {
      setAddingManual(false);
    }
  }, [manualDomain, manualEntryUrls, manualName, onAddSource]);

  return (
    <section className="rounded-xl border border-border/60 bg-card/30 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">Sources & discovery</h2>
          <p className="text-[11px] text-muted-foreground">
            Crawl4AI + expert agent fetch landing pages for the selected horizon. Click Refresh — never runs automatically.
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

      <div className="mt-4 rounded-lg border border-border/50 bg-background/60 p-3">
        <p className="text-[11px] font-medium text-muted-foreground">Add site manually</p>
        <p className="mt-0.5 text-[10px] text-muted-foreground">
          Domain and entry URLs are required so the browse agent can reach forecast pages.
        </p>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-[10px]">
            <span className="font-medium text-muted-foreground">Display name</span>
            <input
              value={manualName}
              onChange={(e) => setManualName(e.target.value)}
              className="rounded-md border border-border/60 bg-background px-2 py-1.5 text-[11px]"
              placeholder="Example Research"
            />
          </label>
          <label className="flex flex-col gap-1 text-[10px]">
            <span className="font-medium text-muted-foreground">Domain</span>
            <input
              value={manualDomain}
              onChange={(e) => setManualDomain(e.target.value)}
              className="rounded-md border border-border/60 bg-background px-2 py-1.5 text-[11px]"
              placeholder="example.com"
            />
          </label>
        </div>
        <label className="mt-2 flex flex-col gap-1 text-[10px]">
          <span className="font-medium text-muted-foreground">Entry URLs (one per line)</span>
          <textarea
            value={manualEntryUrls}
            onChange={(e) => setManualEntryUrls(e.target.value)}
            rows={3}
            className="rounded-md border border-border/60 bg-background px-2 py-1.5 text-[11px]"
            placeholder={"https://example.com/markets\nhttps://example.com/markets/{horizon}d"}
          />
        </label>
        {manualError ? <p className="mt-2 text-[10px] text-red-600 dark:text-red-400">{manualError}</p> : null}
        <button
          type="button"
          onClick={() => void submitManualAdd()}
          disabled={addingManual}
          className="mt-2 inline-flex items-center gap-1 rounded-md bg-primary px-2.5 py-1 text-[10px] font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {addingManual ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
          Add site
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
                  onClick={() => onRequestAdd(row)}
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
                      onRequestAdd({
                        display_name: src.display_name,
                        domains: src.domains,
                        entry_urls: src.entry_urls,
                        id: src.id,
                        kind: src.kind,
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
  onApprovePath?: (sourceId: string) => Promise<void>;
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
  onApprovePath,
}: Props) {
  const [discovering, setDiscovering] = useState(false);
  const [candidates, setCandidates] = useState<Array<Record<string, unknown>>>([]);
  const [busySourceId, setBusySourceId] = useState<string | null>(null);
  const [approvingSourceId, setApprovingSourceId] = useState<string | null>(null);
  const [pendingAddCandidate, setPendingAddCandidate] = useState<Record<string, unknown> | null>(null);
  const [addFlowError, setAddFlowError] = useState<string | null>(null);
  const [addingWithPrompt, setAddingWithPrompt] = useState(false);

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
    async (candidate: Record<string, unknown>, entryUrlsOverride?: string[]) => {
      const key = String(candidate.id ?? candidate.domain ?? candidate.display_name ?? "");
      setBusySourceId(key);
      setAddFlowError(null);
      try {
        const validated = buildAddSourcePayload(candidate, entryUrlsOverride);
        if (!validated.ok) {
          setAddFlowError(validated.error ?? "Invalid add-site request.");
          return;
        }
        await onAddSource(validated.payload!);
        setPendingAddCandidate(null);
      } catch (err) {
        setAddFlowError(err instanceof Error ? err.message : "Failed to add source.");
        throw err;
      } finally {
        setBusySourceId(null);
      }
    },
    [onAddSource],
  );

  const handleRequestAdd = useCallback(
    (candidate: Record<string, unknown>) => {
      setAddFlowError(null);
      if (candidateNeedsEntryUrls(candidate)) {
        setPendingAddCandidate(candidate);
        return;
      }
      void handleAdd(candidate);
    },
    [handleAdd],
  );

  const handlePromptSubmit = useCallback(
    async (entryUrls: string[]) => {
      if (!pendingAddCandidate) return;
      setAddingWithPrompt(true);
      setAddFlowError(null);
      try {
        await handleAdd(pendingAddCandidate, entryUrls);
      } catch {
        // error surfaced via addFlowError
      } finally {
        setAddingWithPrompt(false);
      }
    },
    [handleAdd, pendingAddCandidate],
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

  const handleApprovePath = useCallback(
    async (sourceId: string) => {
      if (!onApprovePath) return;
      setApprovingSourceId(sourceId);
      try {
        await onApprovePath(sourceId);
      } finally {
        setApprovingSourceId(null);
      }
    },
    [onApprovePath],
  );

  const sourceMap = new Map((snapshot?.sources ?? []).map((s) => [s.id, s]));
  const allPredictions = snapshot?.predictions ?? [];
  const visiblePredictions = filterVisiblePredictions(allPredictions);
  const skippedCount = allPredictions.length - visiblePredictions.length;
  const mismatchCount = visiblePredictions.filter((p) => hasHorizonMismatch(p)).length;
  const summary = computeStreetSummary(snapshot, horizonDays);

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-xl border border-border/60 bg-card/30 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold">Street forecast summary</h2>
            <p className="text-[11px] text-muted-foreground">
              {summary.forecastCount} of {summary.watchlistCount} sources · {horizonDays}d horizon
              {summary.spot != null ? ` · NIFTY spot ${summary.spot.toLocaleString("en-IN")}` : ""}
            </p>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Last updated: {fmtTimestamp(summary.fetchedAt ?? undefined)}
          </p>
        </div>
        {summary.targetMedian != null ? (
          <p className="mt-2 text-[12px]">
            Target range{" "}
            <span className="font-semibold tabular-nums">
              {summary.targetMin?.toLocaleString("en-IN")} – {summary.targetMax?.toLocaleString("en-IN")}
            </span>{" "}
            (median {summary.targetMedian.toLocaleString("en-IN")})
          </p>
        ) : (
          <p className="mt-2 text-[11px] text-muted-foreground">
            No cached forecasts — click Refresh to fetch street views.
          </p>
        )}
      </div>

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
        {snapshot?.sources_ok != null ? (
          <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 tabular-nums text-emerald-800 dark:text-emerald-300">
            {snapshot.sources_ok} forecast{snapshot.sources_ok === 1 ? "" : "s"}
          </span>
        ) : null}
        {(snapshot?.sources_error ?? 0) > 0 ? (
          <span className="rounded-full bg-red-500/10 px-2 py-0.5 tabular-nums text-red-700 dark:text-red-300">
            {snapshot.sources_error} crawl error{(snapshot.sources_error ?? 0) === 1 ? "" : "s"}
          </span>
        ) : null}
        {(snapshot?.sources_not_found ?? 0) > 0 ? (
          <span className="rounded-full bg-amber-500/10 px-2 py-0.5 tabular-nums text-amber-800 dark:text-amber-300">
            {snapshot.sources_not_found} no forecast
          </span>
        ) : null}
        {(snapshot?.refresh_attempt_failures ?? 0) > 0 ? (
          <span className="rounded-full bg-amber-500/15 px-2 py-0.5 font-medium text-amber-800 dark:text-amber-300">
            {snapshot.refresh_attempt_failures} refresh failed — cached kept
          </span>
        ) : null}
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
          <ExternalPredictionsComparisonChart snapshot={snapshot} horizonDays={horizonDays} />
          {mismatchCount > 0 ? (
            <p className="text-[11px] text-amber-800 dark:text-amber-300">
              {mismatchCount} source(s) have horizon mismatch — comparison chart uses each article&apos;s target date horizon.
            </p>
          ) : null}
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
            onRequestAdd={handleRequestAdd}
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
                onApprovePath={
                  onApprovePath ? () => handleApprovePath(record.source_id) : undefined
                }
                approvingPath={approvingSourceId === record.source_id}
              />
            ))}
          </div>
        </>
      ) : null}
      {pendingAddCandidate ? (
        <EntryUrlsPrompt
          candidate={pendingAddCandidate}
          onCancel={() => {
            setPendingAddCandidate(null);
            setAddFlowError(null);
          }}
          onSubmit={handlePromptSubmit}
          submitting={addingWithPrompt}
          error={addFlowError}
        />
      ) : null}
      {addFlowError && !pendingAddCandidate ? (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-[12px] text-red-700 dark:text-red-300">
          {addFlowError}
        </div>
      ) : null}
    </div>
  );
}
