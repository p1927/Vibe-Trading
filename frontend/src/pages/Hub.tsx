import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Database, ExternalLink, Loader2, Newspaper, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type HubNewsItem, type HubStatusResponse } from "@/lib/api";

type NewsFilter = "all" | "staging" | "distilled";

function StatCard({
  title,
  children,
  className,
}: {
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("rounded-xl border bg-card p-4 shadow-sm", className)}>
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{title}</p>
      <div className="mt-2">{children}</div>
    </div>
  );
}

function provenanceBadge(provenance?: string) {
  if (provenance === "staging") {
    return (
      <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-800 dark:text-amber-200">
        staging
      </span>
    );
  }
  return (
    <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-300">
      distilled
    </span>
  );
}

function statusBadge(status?: string) {
  const s = (status || "pending").toLowerCase();
  if (s === "approved") {
    return (
      <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] text-emerald-700 dark:text-emerald-300">
        verified
      </span>
    );
  }
  if (s === "partial") {
    return (
      <span className="rounded-full bg-blue-500/10 px-2 py-0.5 text-[10px] text-blue-700 dark:text-blue-300">
        partial
      </span>
    );
  }
  if (s === "pending") {
    return (
      <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
        pending distill
      </span>
    );
  }
  return (
    <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">{s}</span>
  );
}

function NewsRow({ item }: { item: HubNewsItem }) {
  const references = item.references?.length ? item.references : item.sources ?? [];
  const link = item.url || references[0]?.url;

  return (
    <article className="rounded-lg border bg-background/60 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-1.5">
            {provenanceBadge(item.provenance)}
            {statusBadge(item.verification_status)}
            {item.ticker ? (
              <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                {item.ticker}
              </span>
            ) : null}
            {(item.ref_count ?? 0) > 1 ? (
              <span className="text-[10px] tabular-nums text-muted-foreground">
                {item.ref_count} refs
              </span>
            ) : null}
          </div>
          <h3 className="text-[13px] font-semibold leading-snug">{item.title || "Untitled"}</h3>
          <p className="text-[11px] text-muted-foreground">
            {item.source || "unknown source"}
            {item.published_at ? ` · ${item.published_at.slice(0, 16).replace("T", " ")}` : ""}
            {item.ref_id ? ` · ${item.ref_id}` : ""}
          </p>
        </div>
        {link ? (
          <a
            href={link}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex shrink-0 items-center gap-1 rounded-md border px-2 py-1 text-[10px] hover:bg-muted/50"
          >
            Open <ExternalLink className="h-3 w-3" />
          </a>
        ) : null}
      </div>

      {item.summary ? (
        <p className="mt-2 text-[12px] leading-relaxed text-foreground/90 line-clamp-3">{item.summary}</p>
      ) : null}

      {references.length ? (
        <details className="mt-2 text-[11px]">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
            References ({references.length})
          </summary>
          <ul className="mt-1 space-y-1 pl-1">
            {references.map((ref, idx) => {
              const refUrl = ref.url;
              const label = ref.title || ref.publisher || ref.source || ref.vendor || ref.ref_id || `Ref ${idx + 1}`;
              return (
                <li key={`${ref.ref_id || ref.url || idx}`} className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium text-foreground/90">{label}</span>
                  {refUrl ? (
                    <a
                      href={refUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="truncate text-blue-600 hover:underline dark:text-blue-400"
                    >
                      {refUrl}
                    </a>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </details>
      ) : null}

      {(item.tags?.topics?.length ?? 0) > 0 || (item.tags?.factors?.length ?? 0) > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {(item.tags?.topics ?? []).slice(0, 4).map((topic) => (
            <span key={topic} className="rounded bg-blue-500/10 px-1.5 py-0.5 text-[10px] text-blue-700 dark:text-blue-300">
              {topic}
            </span>
          ))}
          {(item.tags?.factors ?? []).slice(0, 4).map((factor) => (
            <span key={factor} className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              {factor}
            </span>
          ))}
        </div>
      ) : null}
    </article>
  );
}

export function Hub() {
  const [data, setData] = useState<HubStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newsFilter, setNewsFilter] = useState<NewsFilter>("all");
  const [showQueue, setShowQueue] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await api.getHubStatus("NIFTY");
      setData(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load hub status");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => {
      void load();
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const hub = data?.hub;
  const staging = hub?.news_staging;
  const newsInventory = hub?.news_inventory;
  const verified = hub?.verified_news;
  const indexResearch = hub?.index_research;
  const constituentCache = hub?.constituent_cache;
  const capture = hub?.capture;
  const factorCoverage = hub?.factor_coverage;

  const filteredNews = useMemo(() => {
    const items = showQueue ? newsInventory?.staging_queue ?? [] : newsInventory?.items ?? [];
    if (newsFilter === "all") return items;
    return items.filter((item) => item.provenance === newsFilter);
  }, [newsFilter, newsInventory?.items, newsInventory?.staging_queue, showQueue]);

  const drainStaging = async () => {
    setBusy("drain");
    setError(null);
    try {
      await api.drainHubStaging("NIFTY", 20);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Staging drain failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Hub inventory</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Live news union (staging refs + distilled hub events), references, cache health, and capture stats.
          </p>
          {hub?.generated_at ? (
            <p className="mt-1 text-[11px] text-muted-foreground">
              Updated {new Date(hub.generated_at).toLocaleString()} · auto-refresh 30s
            </p>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => {
            setLoading(true);
            void load();
          }}
          className="inline-flex h-9 items-center gap-1.5 rounded-lg border bg-background px-3 text-sm hover:bg-muted/50"
        >
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          Refresh
        </button>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      {loading && !hub ? (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading hub status…
        </div>
      ) : null}

      <StatCard title="News & references" className="col-span-full">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Newspaper className="h-4 w-4 text-muted-foreground" />
            <span className="font-semibold tabular-nums text-amber-700 dark:text-amber-300">
              {newsInventory?.pending_count ?? staging?.queued ?? 0} pending in queue
            </span>
            <span className="text-muted-foreground">·</span>
            <span className="tabular-nums">
              {newsInventory?.staging_in_union ?? 0} staging live
            </span>
            <span className="text-muted-foreground">·</span>
            <span className="tabular-nums">
              {newsInventory?.distilled_in_union ?? 0} distilled
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex rounded-lg border p-0.5 text-[11px]">
              {(["all", "staging", "distilled"] as const).map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setNewsFilter(key)}
                  className={cn(
                    "rounded-md px-2 py-1 capitalize",
                    newsFilter === key ? "bg-muted font-medium" : "text-muted-foreground",
                  )}
                >
                  {key}
                </button>
              ))}
            </div>
            <label className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <input
                type="checkbox"
                checked={showQueue}
                onChange={(e) => setShowQueue(e.target.checked)}
                className="rounded border-border"
              />
              Raw queue only
            </label>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void drainStaging()}
              className="rounded-md border px-2 py-1 text-[11px] hover:bg-muted/50 disabled:opacity-50"
            >
              {busy === "drain" ? "Draining…" : "Drain staging (20)"}
            </button>
          </div>
        </div>

        {!filteredNews.length ? (
          <p className="rounded-lg border border-dashed bg-muted/20 px-4 py-8 text-center text-sm text-muted-foreground">
            No headlines in this view. Run analysis with constituents refresh or wait for news ingest — pending refs
            appear here with <span className="font-medium">staging</span> provenance until the worker distills them.
          </p>
        ) : (
          <div className="grid gap-2 md:grid-cols-2">
            {filteredNews.slice(0, 40).map((item) => (
              <NewsRow key={item.id || item.ref_id || item.title} item={item} />
            ))}
          </div>
        )}
      </StatCard>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <StatCard title="Staging queue">
          <div className="space-y-1 text-sm">
            <p>
              <span className="text-muted-foreground">Queued:</span>{" "}
              <span className="font-semibold tabular-nums">{staging?.queued ?? 0}</span>
            </p>
            <p className="text-[11px] text-muted-foreground">
              Entity pipeline: {staging?.entity_pipeline_enabled ? "on" : "off"}
            </p>
            {staging?.worker_last ? (
              <p className="text-[11px] text-muted-foreground">
                Last worker: {staging.worker_last.processed ?? 0} processed
                {(staging.worker_last.errors ?? 0) > 0 ? ` · ${staging.worker_last.errors} errors` : ""}
              </p>
            ) : null}
          </div>
          {(staging?.by_ticker?.length ?? 0) > 0 ? (
            <ul className="mt-3 space-y-1 text-[11px]">
              {staging?.by_ticker?.slice(0, 8).map((row) => (
                <li key={row.ticker} className="flex justify-between gap-2">
                  <span>{row.ticker}</span>
                  <span className="tabular-nums text-muted-foreground">{row.queued}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </StatCard>

        <StatCard title="Verified news (counts)">
          {verified ? (
            <ul className="space-y-2 text-sm">
              {Object.entries(verified).map(([ticker, row]) => (
                <li key={ticker}>
                  <span className="font-medium">{ticker}</span>
                  <span className="ml-2 tabular-nums text-muted-foreground">{row.total} records</span>
                  {row.by_status ? (
                    <p className="text-[11px] text-muted-foreground">
                      {Object.entries(row.by_status)
                        .map(([k, v]) => `${k}: ${String(v)}`)
                        .join(" · ")}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No verified news stats</p>
          )}
        </StatCard>

        <StatCard title="Index research">
          {indexResearch?.present ? (
            <div className="space-y-1 text-sm">
              <p>
                <span className="text-muted-foreground">As of:</span>{" "}
                {indexResearch.as_of ? new Date(indexResearch.as_of).toLocaleString() : "—"}
              </p>
              <p>
                <span className="text-muted-foreground">Horizon:</span>{" "}
                {indexResearch.horizon?.days ?? "—"}d ({indexResearch.horizon?.name ?? "?"})
              </p>
              <p className="text-[11px] text-muted-foreground">
                Last stage: {indexResearch.last_pipeline_stage ?? "—"}
                {indexResearch.last_pipeline_message ? ` — ${indexResearch.last_pipeline_message}` : ""}
              </p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No index snapshot — run analysis on Prediction tab</p>
          )}
        </StatCard>

        <StatCard title="Constituent cache">
          {constituentCache ? (
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div>
                <p className="text-[11px] text-muted-foreground">Fresh</p>
                <p className="text-lg font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">
                  {constituentCache.fresh}
                </p>
              </div>
              <div>
                <p className="text-[11px] text-muted-foreground">Stale</p>
                <p className="text-lg font-semibold tabular-nums text-amber-600 dark:text-amber-400">
                  {constituentCache.stale}
                </p>
              </div>
              <div>
                <p className="text-[11px] text-muted-foreground">Missing</p>
                <p className="text-lg font-semibold tabular-nums">{constituentCache.missing}</p>
              </div>
              <div>
                <p className="text-[11px] text-muted-foreground">Total</p>
                <p className="text-lg font-semibold tabular-nums">{constituentCache.total}</p>
              </div>
            </div>
          ) : null}
        </StatCard>

        <StatCard title="Capture registry">
          <div className="flex items-start gap-2">
            <Database className="mt-0.5 h-4 w-4 text-muted-foreground" aria-hidden />
            <div className="space-y-1 text-sm">
              <p>
                <span className="text-muted-foreground">Enabled:</span>{" "}
                {capture?.stats?.capture_enabled === false ? "no" : "yes"}
              </p>
              {capture?.stats?.channel ? (
                <p className="text-[11px] text-muted-foreground">
                  Hub hits {capture.stats.channel.hub_hits ?? 0} · vendor fetches{" "}
                  {capture.stats.channel.vendor_fetches ?? 0}
                </p>
              ) : null}
            </div>
          </div>
        </StatCard>

        <StatCard title="Factor coverage">
          {factorCoverage ? (
            <div className="space-y-1 text-sm">
              <p>
                <span className="text-muted-foreground">Gate min:</span>{" "}
                <span className="font-semibold tabular-nums">{factorCoverage.min_pct ?? 0}%</span>
                {" · "}
                {factorCoverage.passes_gate ? "pass" : "fail"}
              </p>
              <p className="text-[11px] text-muted-foreground">
                Trading days {factorCoverage.trading_days ?? 0}
                {factorCoverage.start && factorCoverage.end
                  ? ` · ${factorCoverage.start} → ${factorCoverage.end}`
                  : ""}
              </p>
            </div>
          ) : null}
        </StatCard>
      </div>

      {hub?.paths ? (
        <StatCard title="Hub paths">
          <ul className="space-y-1 font-mono text-[11px] text-muted-foreground">
            {Object.entries(hub.paths).map(([key, path]) => (
              <li key={key}>
                <span className="text-foreground">{key}</span>: {path}
              </li>
            ))}
          </ul>
        </StatCard>
      ) : null}
    </div>
  );
}

export default Hub;
