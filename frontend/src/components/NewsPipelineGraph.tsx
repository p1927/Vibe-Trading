import { useCallback, useEffect, useState } from "react";
import { ExternalLink, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  api,
  type HubNewsPipelineTraceItem,
  type HubNewsPipelineTraceSummary,
} from "@/lib/api";

const SOURCES = [
  { key: "rss", label: "RSS" },
  { key: "searxng", label: "SearXNG" },
  { key: "searxng_global", label: "SearXNG global" },
  { key: "moneycontrol", label: "Moneycontrol" },
  { key: "watcher", label: "Watcher" },
];

const STAGES = [
  { key: "step_01_relevance_gate", label: "Relevance gate" },
  { key: "step_02_fetch_http", label: "Fetch (HTTP)" },
  { key: "step_02b_fetch_crawl4ai", label: "Fetch (Crawl4AI)" },
  { key: "step_03_datetime_normalize", label: "Normalize date" },
  { key: "step_04_ref_enrich_llm", label: "LLM enrich + dedup" },
  { key: "step_05_claims_bridge", label: "Claim extraction" },
  { key: "step_06_adjudicate_bridge", label: "Adjudicate" },
  { key: "step_07_event_distill_bridge", label: "Distill event" },
];

type SelectedNode =
  | { kind: "source"; key: string; label: string }
  | { kind: "stage"; key: string; label: string; status?: string }
  | { kind: "final"; key: "resolved" | "discarded"; label: string };

function stageCount(summary: HubNewsPipelineTraceSummary | null, stageKey: string): number {
  const bucket = summary?.by_stage?.[stageKey];
  if (!bucket) return 0;
  return Object.values(bucket).reduce((sum, n) => sum + (n || 0), 0);
}

function stageFailedCount(summary: HubNewsPipelineTraceSummary | null, stageKey: string): number {
  const bucket = summary?.by_stage?.[stageKey];
  if (!bucket) return 0;
  return (bucket.failed ?? 0) + (bucket.discarded ?? 0);
}

function NodeButton({
  active,
  label,
  count,
  subCount,
  onClick,
}: {
  active: boolean;
  label: string;
  count: number;
  subCount?: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full rounded-lg border px-2.5 py-1.5 text-left text-[11px] transition-colors",
        active ? "border-primary bg-primary/10" : "hover:bg-muted/50",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium leading-tight">{label}</span>
        <span className="shrink-0 tabular-nums text-muted-foreground">{count}</span>
      </div>
      {subCount ? (
        <span className="text-[10px] text-red-700 dark:text-red-300">{subCount} discarded/failed</span>
      ) : null}
    </button>
  );
}

function stepStatusTone(status?: string): string {
  const s = (status || "").toLowerCase();
  if (s === "ok") return "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
  if (s === "skipped") return "bg-muted text-muted-foreground";
  if (s === "failed" || s === "discarded") return "bg-red-500/10 text-red-700 dark:text-red-300";
  return "bg-muted text-muted-foreground";
}

function TraceItemRow({ item }: { item: HubNewsPipelineTraceItem }) {
  return (
    <article className="rounded-lg border bg-background/60 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
              {item.source || "unknown source"}
            </span>
            <span
              className={cn(
                "rounded-full px-2 py-0.5 text-[10px] font-medium capitalize",
                item.final_status === "discarded"
                  ? "bg-red-500/15 text-red-800 dark:text-red-200"
                  : "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
              )}
            >
              {item.final_status || "unknown"}
            </span>
          </div>
          <h3 className="text-[13px] font-semibold leading-snug">{item.title || "Untitled"}</h3>
          {item.discard_reason ? (
            <p className="text-[11px] text-muted-foreground">Discard reason: {item.discard_reason}</p>
          ) : null}
        </div>
        {item.url ? (
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex shrink-0 items-center gap-1 rounded-md border px-2 py-1 text-[10px] hover:bg-muted/50"
          >
            Open <ExternalLink className="h-3 w-3" />
          </a>
        ) : null}
      </div>
      {item.steps?.length ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {item.steps.map((step, idx) => (
            <span
              key={`${step.step_id || "step"}-${idx}`}
              title={step.error || step.status}
              className={cn("rounded px-1.5 py-0.5 text-[10px]", stepStatusTone(step.status))}
            >
              {(step.step_id || "").replace(/^step_\d+b?_/, "")}
            </span>
          ))}
        </div>
      ) : null}
    </article>
  );
}

export function NewsPipelineGraph({ entityId = "NIFTY" }: { entityId?: string }) {
  const [summary, setSummary] = useState<HubNewsPipelineTraceSummary | null>(null);
  const [selected, setSelected] = useState<SelectedNode | null>(null);
  const [items, setItems] = useState<HubNewsPipelineTraceItem[]>([]);
  const [loadingSummary, setLoadingSummary] = useState(true);
  const [loadingItems, setLoadingItems] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSummary = useCallback(async () => {
    setLoadingSummary(true);
    setError(null);
    try {
      const res = await api.getHubNewsPipelineTraceSummary(entityId);
      setSummary(res.summary ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load pipeline summary");
    } finally {
      setLoadingSummary(false);
    }
  }, [entityId]);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  const selectNode = useCallback(
    async (node: SelectedNode) => {
      setSelected(node);
      setLoadingItems(true);
      setError(null);
      try {
        const res = await api.listHubNewsPipelineTraceItems({
          entityId,
          source: node.kind === "source" ? node.key : undefined,
          stage: node.kind === "stage" ? node.key : undefined,
          status: node.kind === "stage" ? node.status : undefined,
          limit: 40,
        });
        let list = res.items ?? [];
        if (node.kind === "final") {
          list = list.filter((it) => it.final_status === node.key);
        }
        setItems(list);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load pipeline items");
      } finally {
        setLoadingItems(false);
      }
    },
    [entityId],
  );

  const resolvedCount = summary?.by_final_status?.resolved ?? 0;
  const discardedCount = summary?.by_final_status?.discarded ?? 0;

  return (
    <div className="grid gap-3 md:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      <div className="space-y-3 rounded-lg border bg-muted/10 p-3">
        {loadingSummary && !summary ? (
          <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Loading pipeline trace…
          </div>
        ) : null}
        {!loadingSummary && summary?.total === 0 ? (
          <p className="text-[11px] text-muted-foreground">
            No traced items yet — pipeline trace is recorded as new refs are resolved. Run an ingest to populate this
            view.
          </p>
        ) : null}
        <div className="grid grid-cols-3 gap-2">
          <div className="space-y-1">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Sources</p>
            {SOURCES.map((s) => (
              <NodeButton
                key={s.key}
                active={selected?.kind === "source" && selected.key === s.key}
                label={s.label}
                count={summary?.by_source?.[s.key] ?? 0}
                onClick={() => void selectNode({ kind: "source", key: s.key, label: s.label })}
              />
            ))}
          </div>
          <div className="space-y-1">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Stages</p>
            {STAGES.map((s) => (
              <NodeButton
                key={s.key}
                active={selected?.kind === "stage" && selected.key === s.key}
                label={s.label}
                count={stageCount(summary, s.key)}
                subCount={stageFailedCount(summary, s.key)}
                onClick={() => void selectNode({ kind: "stage", key: s.key, label: s.label })}
              />
            ))}
          </div>
          <div className="space-y-1">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">Outcome</p>
            <NodeButton
              active={selected?.kind === "final" && selected.key === "resolved"}
              label="Resolved"
              count={resolvedCount}
              onClick={() => void selectNode({ kind: "final", key: "resolved", label: "Resolved" })}
            />
            <NodeButton
              active={selected?.kind === "final" && selected.key === "discarded"}
              label="Discarded"
              count={discardedCount}
              onClick={() => void selectNode({ kind: "final", key: "discarded", label: "Discarded" })}
            />
          </div>
        </div>
      </div>

      <div className="min-h-[200px] space-y-2">
        {!selected ? (
          <p className="rounded-lg border border-dashed bg-muted/20 px-4 py-8 text-center text-[12px] text-muted-foreground">
            Click a source, stage, or outcome node to see the items that passed through it.
          </p>
        ) : (
          <>
            <p className="text-[11px] font-medium text-muted-foreground">{selected.label}</p>
            {loadingItems ? (
              <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Loading items…
              </div>
            ) : items.length ? (
              <div className="space-y-2">
                {items.map((item, idx) => (
                  <TraceItemRow key={`${item.ref_id || "item"}-${idx}`} item={item} />
                ))}
              </div>
            ) : (
              <p className="rounded-lg border border-dashed bg-muted/20 px-4 py-8 text-center text-[12px] text-muted-foreground">
                No traced items at this node yet.
              </p>
            )}
          </>
        )}
        {error ? <p className="text-[11px] text-destructive">{error}</p> : null}
      </div>
    </div>
  );
}
