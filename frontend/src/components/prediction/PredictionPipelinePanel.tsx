import { cn } from "@/lib/utils";
import type { IndexFactorCatalogEntry, IndexPredictionArtifact, PipelineLogEntry } from "@/lib/api";
import {
  formatPipelineLogTime,
  pickDisplayPipelineLogs,
  pipelineLogRowKey,
} from "@/lib/pipelineLogUtils";
import { MACRO_MODEL_KEYS } from "@/lib/predictionVerification";
import { ChevronDown, ChevronRight, Loader2, Terminal } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

const LEVEL_STYLES: Record<string, string> = {
  info: "text-foreground/90",
  warn: "text-amber-700 dark:text-amber-400",
  error: "text-red-700 dark:text-red-400",
};

const PIPELINE_STAGE_ORDER = [
  "start",
  "history",
  "data_completeness",
  "constituents",
  "ohlcv_cache",
  "momentum",
  "macro",
  "alpha_zoo",
  "spot",
  "regime",
  "scenarios",
  "predict",
  "attribution",
  "reconcile",
  "debate",
  "forecast_lab",
  "explain",
  "accuracy",
  "ledger",
  "done",
  "events",
  "news_impact",
  "meta",
  "persist",
  "cancel",
  "error",
] as const;

function pipelineProgressFromLogs(logs: PipelineLogEntry[]): {
  stage: string | null;
  pct: number;
  elapsedMs: number | null;
} {
  if (!logs.length) {
    return { stage: null, pct: 0, elapsedMs: null };
  }
  let maxIdx = -1;
  for (const entry of logs) {
    const stage = entry.stage ?? "";
    const idx = PIPELINE_STAGE_ORDER.indexOf(stage as (typeof PIPELINE_STAGE_ORDER)[number]);
    if (idx > maxIdx) maxIdx = idx;
  }
  const latest = logs[logs.length - 1]!;
  const stage = latest.stage ?? null;
  const pct =
    maxIdx >= 0
      ? Math.round(((maxIdx + 1) / PIPELINE_STAGE_ORDER.length) * 100)
      : Math.min(95, Math.max(5, Math.round((logs.length / 40) * 100)));
  const elapsedMs =
    typeof latest.detail?.elapsed_ms === "number" ? latest.detail.elapsed_ms : null;
  return { stage, pct: Math.min(100, pct), elapsedMs };
}

interface Props {
  open: boolean;
  running: boolean;
  reattached?: boolean;
  streamReconnecting?: boolean;
  runJobId?: string | null;
  logs: PipelineLogEntry[];
  artifact?: IndexPredictionArtifact | null;
  factorCatalog: {
    macro_and_technical?: IndexFactorCatalogEntry[];
    bottom_up?: IndexFactorCatalogEntry[];
    constituent_research?: IndexFactorCatalogEntry[];
    constituent_market_data?: IndexFactorCatalogEntry[];
    news_and_sentiment?: IndexFactorCatalogEntry[];
    derivatives?: IndexFactorCatalogEntry[];
    pipeline_modules?: IndexFactorCatalogEntry[];
    model_layers?: IndexFactorCatalogEntry[];
  } | null;
  catalogLoading?: boolean;
}

function FactorList({
  title,
  items,
  activeKeys,
}: {
  title: string;
  items: IndexFactorCatalogEntry[];
  activeKeys: Set<string>;
}) {
  const [expanded, setExpanded] = useState(title.includes("Macro"));
  if (!items.length) return null;
  return (
    <div className="border-t border-border/60 pt-2">
      <button
        type="button"
        className="flex w-full items-center gap-1 text-left text-[10px] font-semibold uppercase tracking-wide text-muted-foreground"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        {title} ({items.length})
      </button>
      {expanded ? (
        <ul className="mt-1.5 space-y-2">
          {items.map((f) => {
            const inModel = activeKeys.has(f.key);
            return (
              <li
                key={f.key}
                className={cn(
                  "rounded-md px-2 py-1.5",
                  inModel ? "bg-emerald-500/10 ring-1 ring-emerald-500/20" : "bg-muted/40",
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <p className="text-[11px] font-medium">{f.label}</p>
                  <div className="flex shrink-0 flex-col items-end gap-0.5">
                    {inModel ? (
                      <span className="text-[9px] font-medium text-emerald-700 dark:text-emerald-400">
                        in model
                      </span>
                    ) : null}
                    {f.data_quality === "proxy" ? (
                      <span className="text-[9px] font-medium text-amber-700 dark:text-amber-400">
                        proxy data
                      </span>
                    ) : null}
                  </div>
                </div>
                <p className="text-[10px] text-muted-foreground">
                  <span className="font-mono">{f.key}</span>
                  {f.source ? ` · ${f.source}` : null}
                </p>
                {f.role ? <p className="mt-0.5 text-[10px] leading-snug text-muted-foreground">{f.role}</p> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

export function PredictionPipelinePanel({
  open,
  running,
  reattached,
  streamReconnecting,
  runJobId,
  logs,
  artifact,
  factorCatalog,
  catalogLoading,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const displayLogs = useMemo(
    () => pickDisplayPipelineLogs(logs, artifact?.pipeline_log, running, artifact?.as_of),
    [logs, artifact?.pipeline_log, artifact?.as_of, running],
  );

  const progress = useMemo(
    () => (running ? pipelineProgressFromLogs(logs) : { stage: null, pct: 100, elapsedMs: null }),
    [logs, running],
  );

  const activeFactorKeys = useMemo(() => {
    const keys = new Set<string>(MACRO_MODEL_KEYS as readonly string[]);
    for (const gf of artifact?.global_factors ?? []) {
      if (gf.factor) keys.add(gf.factor);
    }
    for (const c of artifact?.factor_explanation?.contributors ?? []) {
      if (c.factor) keys.add(c.factor);
    }
    return keys;
  }, [artifact]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [displayLogs.length, running]);

  if (!open) return null;

  return (
    <aside
      className={cn(
        "flex h-[calc(100vh-5rem)] w-full shrink-0 flex-col overflow-hidden rounded-xl border bg-card shadow-sm",
        "lg:sticky lg:top-4 lg:w-80 xl:w-96",
      )}
      aria-label="Pipeline activity"
    >
      <div className="flex items-center gap-2 border-b px-3 py-2.5">
        <Terminal className="h-4 w-4 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">Pipeline activity</p>
          <p className="text-[10px] text-muted-foreground">
            {running
              ? "Running analysis now — live steps below"
              : artifact?.as_of
                ? `Last run ${new Date(artifact.as_of).toLocaleString("en-IN", { dateStyle: "short", timeStyle: "short" })}`
                : "Live steps when you run analysis"}
          </p>
        </div>
        {running ? <Loader2 className="h-4 w-4 animate-spin text-primary" /> : null}
      </div>

      {reattached && runJobId ? (
        <div className="border-b bg-muted/40 px-3 py-1.5 text-[10px] text-muted-foreground">
          Reconnected to run {runJobId.slice(0, 12)}…
        </div>
      ) : null}

      {streamReconnecting ? (
        <div className="border-b bg-amber-500/10 px-3 py-1.5 text-[10px] text-amber-800 dark:text-amber-300">
          Stream dropped — still running, polling for updates…
        </div>
      ) : null}

      {running ? (
        <div className="border-b px-3 py-2">
          <div className="mb-1 flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
            <span className="font-medium uppercase tracking-wide">
              {progress.stage ?? "starting"}
            </span>
            <span>{progress.pct}%</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-all duration-300"
              style={{ width: `${progress.pct}%` }}
            />
          </div>
          {progress.elapsedMs != null ? (
            <p className="mt-1 text-[10px] text-muted-foreground">
              Current step {(progress.elapsedMs / 1000).toFixed(1)}s
            </p>
          ) : null}
        </div>
      ) : null}

      {artifact ? (
        <div className="border-b px-3 py-2 text-[10px] text-muted-foreground">
          Spot {artifact.spot?.toLocaleString("en-IN")} ·{" "}
          {artifact.factor_explanation?.contributors?.length ?? 0} contributors ·{" "}
          {artifact.constituent_signals?.length ?? 0} constituents
        </div>
      ) : null}

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-2 font-mono text-[11px]">
        {displayLogs.length === 0 && !running ? (
          <p className="py-4 text-center text-muted-foreground">
            Run analysis to see pipeline steps. Cached artifacts show stage summary after reload.
          </p>
        ) : null}
        <ul className="space-y-1.5">
          {displayLogs.map((entry, idx) => {
            const detail = entry.detail ?? {};
            const elapsedMs = detail.elapsed_ms;
            const cached = detail.cached;
            const timing =
              typeof elapsedMs === "number"
                ? ` (${(elapsedMs / 1000).toFixed(1)}s)`
                : cached === true
                  ? " (cached)"
                  : "";
            return (
            <li key={pipelineLogRowKey(entry, idx)} className="leading-snug">
              <span className="text-[9px] text-muted-foreground">
                {formatPipelineLogTime(entry.at)}
              </span>
              <span className="mx-1 rounded bg-muted px-1 text-[9px] uppercase">{entry.stage}</span>
              <span className={LEVEL_STYLES[entry.level] ?? LEVEL_STYLES.info}>
                {entry.message}
                {timing ? (
                  <span className="text-muted-foreground">{timing}</span>
                ) : null}
              </span>
            </li>
            );
          })}
        </ul>
        {running && logs.length === 0 ? (
          <p className="mt-2 animate-pulse text-muted-foreground">Starting pipeline…</p>
        ) : null}
      </div>

      <div className="max-h-[45%] overflow-y-auto border-t px-3 py-2">
        <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          NIFTY 50 factor catalog
        </p>
        {catalogLoading ? (
          <p className="text-[11px] text-muted-foreground">Loading factors…</p>
        ) : factorCatalog ? (
          <>
            <FactorList
              title="Macro & technical"
              items={factorCatalog.macro_and_technical ?? []}
              activeKeys={activeFactorKeys}
            />
            <FactorList title="Derivatives" items={factorCatalog.derivatives ?? []} activeKeys={activeFactorKeys} />
            <FactorList title="Bottom-up signals" items={factorCatalog.bottom_up ?? []} activeKeys={activeFactorKeys} />
            <FactorList
              title="Constituent research"
              items={factorCatalog.constituent_research ?? []}
              activeKeys={activeFactorKeys}
            />
            <FactorList
              title="News & sentiment"
              items={factorCatalog.news_and_sentiment ?? []}
              activeKeys={activeFactorKeys}
            />
            <FactorList
              title="Pipeline modules"
              items={factorCatalog.pipeline_modules ?? []}
              activeKeys={activeFactorKeys}
            />
            <FactorList title="Model layers" items={factorCatalog.model_layers ?? []} activeKeys={activeFactorKeys} />
          </>
        ) : (
          <p className="text-[11px] text-muted-foreground">Factor list unavailable.</p>
        )}
      </div>
    </aside>
  );
}
