import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, ApiError } from "@/lib/api";
import { useLatestRequest } from "@/hooks/useLatestRequest";
import type {
  FinancialKnowledgeCuratorReport,
  NewsDerivedConcept,
  StrategyEntry,
  WikiEntry,
} from "@/lib/knowledgeEngine";

type Tab = "wiki" | "strategies" | "news" | "corpus";

const TABS = [
  { key: "wiki", label: "knowledgeEngine.tabWiki" },
  { key: "strategies", label: "knowledgeEngine.tabStrategies" },
  { key: "news", label: "knowledgeEngine.tabNews" },
  { key: "corpus", label: "knowledgeEngine.tabCorpus" },
] as const satisfies readonly { key: Tab; label: string }[];

function ScoreBadge({ score }: { score?: number | null }) {
  return (
    <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
      {typeof score === "number" && Number.isFinite(score) ? score.toFixed(2) : "—"}
    </span>
  );
}

function TagList({ tags }: { tags?: string[] }) {
  if (!tags?.length) return null;
  return (
    <div className="mt-1.5 flex flex-wrap gap-1">
      {tags.map((tag) => (
        <span key={tag} className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
          {tag}
        </span>
      ))}
    </div>
  );
}

function SearchBar({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return (
    <div className="relative">
      <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-md border border-border/60 bg-background py-2 pl-8 pr-3 text-sm outline-none focus:border-primary"
      />
    </div>
  );
}

function WikiTab() {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const [results, setResults] = useState<WikiEntry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openSlug, setOpenSlug] = useState<string | null>(null);
  const [pageContent, setPageContent] = useState<Record<string, string>>({});
  const [pageNote, setPageNote] = useState<Record<string, string>>({});
  const [pageLoading, setPageLoading] = useState<string | null>(null);

  const begin = useLatestRequest();
  const load = useCallback(async (q: string) => {
    const isCurrent = begin();
    setLoading(true);
    try {
      const res = await api.queryKnowledgeWiki({ text: q || undefined, limit: 20 });
      if (!isCurrent()) return;
      setError(null);
      setResults(res.results);
    } catch (e) {
      if (isCurrent()) setError(e instanceof ApiError ? e.message : t("knowledgeEngine.requestFailed"));
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, [begin]);

  useEffect(() => {
    const timer = setTimeout(() => void load(text), 300);
    return () => clearTimeout(timer);
  }, [text, load]);

  const toggle = async (slug: string) => {
    if (openSlug === slug) {
      setOpenSlug(null);
      return;
    }
    setOpenSlug(slug);
    if (!pageContent[slug]) {
      setPageLoading(slug);
      try {
        const page = await api.getKnowledgeWikiPage(slug);
        if (page.found) {
          setPageContent((prev) => ({ ...prev, [slug]: page.content ?? "" }));
          setPageNote((prev) => ({ ...prev, [slug]: "" }));
        } else {
          setPageNote((prev) => ({ ...prev, [slug]: t("knowledgeEngine.pageNotFound") }));
        }
      } catch (e) {
        setPageNote((prev) => ({
          ...prev,
          [slug]: e instanceof ApiError ? t("knowledgeEngine.loadFailedWith", { message: e.message }) : t("knowledgeEngine.loadFailed"),
        }));
      } finally {
        setPageLoading(null);
      }
    }
  };

  return (
    <div className="space-y-3">
      <SearchBar value={text} onChange={setText} placeholder={t("knowledgeEngine.searchWiki")} />
      {error && <div className="rounded border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{error}</div>}
      {loading && !results && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("knowledgeEngine.loading")}
        </div>
      )}
      {!loading && results && results.length === 0 && (
        <p className="text-sm text-muted-foreground">{t("knowledgeEngine.noWiki")}</p>
      )}
      <div className="space-y-2">
        {results?.map((entry) => (
          <div key={entry.slug} className="rounded-lg border border-border/60 bg-muted/20 p-3">
            <button
              type="button"
              onClick={() => void toggle(entry.slug)}
              className="flex w-full items-start justify-between gap-2 text-left"
            >
              <div>
                <div className="text-sm font-semibold">{entry.title}</div>
                <p className="mt-0.5 text-xs text-muted-foreground">{entry.summary}</p>
                <TagList tags={entry.tags} />
              </div>
              <ScoreBadge score={entry.score} />
            </button>
            {openSlug === entry.slug && (
              <div className="mt-3 border-t border-border/60 pt-3 text-xs">
                {pageLoading === entry.slug ? (
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> {t("knowledgeEngine.loadingPage")}
                  </div>
                ) : (
                  <pre className="whitespace-pre-wrap font-sans text-xs leading-relaxed text-foreground">
                    {pageNote[entry.slug] || pageContent[entry.slug]}
                  </pre>
                )}
                {entry.sources?.length ? (
                  <div className="mt-2 text-[10px] text-muted-foreground">
                    {t("knowledgeEngine.sources", { list: entry.sources.join(", ") })}
                  </div>
                ) : null}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function StrategyCard({ strategy }: { strategy: StrategyEntry }) {
  const { t } = useTranslation();
  return (
    <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="text-sm font-semibold">{strategy.label ?? strategy.key}</div>
        <ScoreBadge score={strategy.score} />
      </div>
      {strategy.logic && <p className="mt-1 text-xs text-muted-foreground">{strategy.logic}</p>}
      {strategy.when && (
        <p className="mt-1 text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground">{t("knowledgeEngine.when")} </span>
          {strategy.when}
        </p>
      )}
      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-muted-foreground sm:grid-cols-3">
        {strategy.market_view && (
          <div>
            {t("knowledgeEngine.marketView")} <span className="font-medium text-foreground">{strategy.market_view}</span>
          </div>
        )}
        {strategy.risk_profile && (
          <div>
            {t("knowledgeEngine.risk")} <span className="font-medium text-foreground">{strategy.risk_profile}</span>
          </div>
        )}
        {strategy.horizon_fit && (
          <div>
            {t("knowledgeEngine.horizon")} <span className="font-medium text-foreground">{strategy.horizon_fit}</span>
          </div>
        )}
      </div>
      {strategy.indicators_to_watch?.length ? (
        <TagList tags={strategy.indicators_to_watch} />
      ) : null}
    </div>
  );
}

function StrategiesTab() {
  const { t } = useTranslation();
  const [marketView, setMarketView] = useState("");
  const [riskProfile, setRiskProfile] = useState("");
  const [results, setResults] = useState<StrategyEntry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const begin = useLatestRequest();
  const load = useCallback(async () => {
    const isCurrent = begin();
    setLoading(true);
    try {
      const res = await api.queryKnowledgeStrategies({
        marketView: marketView || undefined,
        riskProfile: riskProfile || undefined,
        limit: 20,
      });
      if (!isCurrent()) return;
      setError(null);
      setResults(res.results);
    } catch (e) {
      if (isCurrent()) setError(e instanceof ApiError ? e.message : t("knowledgeEngine.requestFailed"));
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, [marketView, riskProfile, begin]);

  useEffect(() => {
    const timer = setTimeout(() => void load(), 300);
    return () => clearTimeout(timer);
  }, [load]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <input
          type="text"
          value={marketView}
          onChange={(e) => setMarketView(e.target.value)}
          placeholder={t("knowledgeEngine.marketViewPlaceholder")}
          className="w-48 rounded-md border border-border/60 bg-background px-3 py-2 text-sm outline-none focus:border-primary"
        />
        <input
          type="text"
          value={riskProfile}
          onChange={(e) => setRiskProfile(e.target.value)}
          placeholder={t("knowledgeEngine.riskPlaceholder")}
          className="w-48 rounded-md border border-border/60 bg-background px-3 py-2 text-sm outline-none focus:border-primary"
        />
      </div>
      {error && <div className="rounded border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{error}</div>}
      {loading && !results && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("knowledgeEngine.loading")}
        </div>
      )}
      {!loading && results && results.length === 0 && (
        <p className="text-sm text-muted-foreground">{t("knowledgeEngine.noStrategies")}</p>
      )}
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {results?.map((s) => (
          <StrategyCard key={s.key} strategy={s} />
        ))}
      </div>
    </div>
  );
}

function NewsConceptCard({ concept, index }: { concept: NewsDerivedConcept; index: number }) {
  const { t } = useTranslation();
  return (
    <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="text-sm font-semibold">
          {concept.tactic_kind ?? concept.instrument ?? t("knowledgeEngine.conceptN", { n: index + 1 })}
        </div>
        <ScoreBadge score={concept.score} />
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{concept.text}</p>
      {concept.trigger_context && (
        <p className="mt-1 text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground">{t("knowledgeEngine.trigger")} </span>
          {concept.trigger_context}
        </p>
      )}
      <TagList tags={concept.tags} />
      {concept.source_citation && (
        <div className="mt-2 text-[10px] text-muted-foreground">{t("knowledgeEngine.source", { citation: concept.source_citation })}</div>
      )}
    </div>
  );
}

function NewsDerivedTab() {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const [results, setResults] = useState<NewsDerivedConcept[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const begin = useLatestRequest();
  const load = useCallback(async (q: string) => {
    const isCurrent = begin();
    setLoading(true);
    try {
      const res = await api.queryKnowledgeNewsDerivedStrategies({ text: q || undefined, limit: 20 });
      if (!isCurrent()) return;
      setError(null);
      setResults(res.results);
    } catch (e) {
      if (isCurrent()) setError(e instanceof ApiError ? e.message : t("knowledgeEngine.requestFailed"));
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, [begin]);

  useEffect(() => {
    const timer = setTimeout(() => void load(text), 300);
    return () => clearTimeout(timer);
  }, [text, load]);

  return (
    <div className="space-y-3">
      <SearchBar value={text} onChange={setText} placeholder={t("knowledgeEngine.searchNews")} />
      {error && <div className="rounded border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{error}</div>}
      {loading && !results && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("knowledgeEngine.loading")}
        </div>
      )}
      {!loading && results && results.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {t("knowledgeEngine.noNews")}
        </p>
      )}
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {results?.map((c, i) => (
          <NewsConceptCard key={`${c.text}-${i}`} concept={c} index={i} />
        ))}
      </div>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes)) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let value = Math.abs(bytes);
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const sign = bytes < 0 ? "-" : "";
  return `${sign}${value.toFixed(1)} ${units[unitIndex]}`;
}

function formatDelta(delta: number): string {
  if (delta === 0) return "±0";
  return delta > 0 ? `+${delta}` : String(delta);
}

/**
 * Corpus Health tab (Part B of
 * .claude/backlog/items/2026-09-02-wiki-lifecycle-knowledge-bridge.md) — the
 * financial-knowledge curator's latest persisted report: flagged low-value raw
 * sources, flagged undistilled wiki pages, per-directory size/growth, and
 * whether the last run actually triggered the LLM-Wiki app's ingest. Reads a
 * pre-computed report only — never triggers a live curation run from the UI
 * (that would pay for an LLM-judge batch on every page load).
 */
function CorpusHealthTab() {
  const { t } = useTranslation();
  const [report, setReport] = useState<FinancialKnowledgeCuratorReport | null>(null);
  const [hasRun, setHasRun] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getFinancialKnowledgeStatus()
      .then((res) => {
        if (cancelled) return;
        setError(null);
        setHasRun(res.has_run);
        setReport(res.report);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.message : t("knowledgeEngine.requestFailed"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> {t("knowledgeEngine.loading")}
      </div>
    );
  }
  if (error) {
    return <div className="rounded border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{error}</div>;
  }
  if (!hasRun || !report) {
    return (
      <p className="text-sm text-muted-foreground">
        {t("knowledgeEngine.curatorNotRun")}
      </p>
    );
  }
  if (report.skipped) {
    return (
      <div className="rounded-lg border border-border/60 bg-muted/20 p-3 text-sm">
        <div className="font-medium text-foreground">{t("knowledgeEngine.lastRunSkipped")}</div>
        <p className="mt-1 text-xs text-muted-foreground">
          {report.reason === "no_generative_adapter_configured"
            ? t("knowledgeEngine.noGenerativeAdapter")
            : report.reason}
        </p>
        <p className="mt-2 text-[10px] text-muted-foreground">{t("knowledgeEngine.ranAt", { at: report.ran_at })}</p>
      </div>
    );
  }

  const buckets = Object.entries(report.growth?.buckets ?? {}).sort((a, b) => b[1].bytes - a[1].bytes);

  return (
    <div className="space-y-4">
      <div className="text-[10px] text-muted-foreground">{t("knowledgeEngine.lastRun", { at: report.ran_at })}</div>

      <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
        <div className="text-sm font-semibold">{t("knowledgeEngine.corpusSize")}</div>
        {buckets.length === 0 ? (
          <p className="mt-1 text-xs text-muted-foreground">{t("knowledgeEngine.noSnapshot")}</p>
        ) : (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="pb-1 pr-4 font-medium">{t("knowledgeEngine.colDirectory")}</th>
                  <th className="pb-1 pr-4 font-medium">{t("knowledgeEngine.colFiles")}</th>
                  <th className="pb-1 pr-4 font-medium">{t("knowledgeEngine.colSize")}</th>
                  <th className="pb-1 font-medium">{t("knowledgeEngine.colChange")}</th>
                </tr>
              </thead>
              <tbody>
                {buckets.map(([name, bucket]) => (
                  <tr key={name} className="border-t border-border/40">
                    <td className="py-1 pr-4 font-medium text-foreground">{name}</td>
                    <td className="py-1 pr-4">
                      {bucket.files}{" "}
                      <span className="text-muted-foreground">({formatDelta(bucket.files_delta)})</span>
                    </td>
                    <td className="py-1 pr-4">{formatBytes(bucket.bytes)}</td>
                    <td className="py-1 text-muted-foreground">
                      {bucket.bytes_delta === 0 ? "±0 B" : `${bucket.bytes_delta > 0 ? "+" : "-"}${formatBytes(Math.abs(bucket.bytes_delta))}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
        <div className="text-sm font-semibold">
          {t("knowledgeEngine.flaggedSources")}{" "}
          {report.sources && (
            <span className="font-normal text-muted-foreground">
              {t("knowledgeEngine.flaggedCounts", { flagged: report.sources.flagged_count, remaining: report.sources.remaining })}
            </span>
          )}
        </div>
        {!report.sources?.flagged.length ? (
          <p className="mt-1 text-xs text-muted-foreground">{t("knowledgeEngine.noFlaggedSources")}</p>
        ) : (
          <ul className="mt-2 space-y-1.5">
            {report.sources.flagged.map((s) => (
              <li key={s.path} className="text-xs">
                <span className="font-medium text-foreground">{s.path}</span>
                <span className="text-muted-foreground"> — {s.reason}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
        <div className="text-sm font-semibold">
          {t("knowledgeEngine.undistilled")}{" "}
          {report.distillation && (
            <span className="font-normal text-muted-foreground">
              {t("knowledgeEngine.undistilledCounts", {
                flagged: report.distillation.flagged_count,
                threshold: report.distillation.line_threshold,
                scanned: report.distillation.scanned,
              })}
            </span>
          )}
        </div>
        {!report.distillation?.flagged.length ? (
          <p className="mt-1 text-xs text-muted-foreground">{t("knowledgeEngine.noFlaggedPages")}</p>
        ) : (
          <ul className="mt-2 space-y-1.5">
            {report.distillation.flagged.slice(0, 25).map((p) => (
              <li key={p.path} className="text-xs">
                <span className="font-medium text-foreground">{p.path}</span>
                <span className="text-muted-foreground"> — {t("knowledgeEngine.linesN", { n: p.lines })}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {report.ingest && (
        <div className="text-[10px] text-muted-foreground">
          {t("knowledgeEngine.ingestTrigger")}{" "}
          {report.ingest.skipped
            ? t("knowledgeEngine.ingestSkipped", { reason: report.ingest.reason })
            : report.ingest.ok
              ? t("knowledgeEngine.ingestTriggered")
              : t("knowledgeEngine.ingestFailed", { error: report.ingest.error })}
        </div>
      )}
    </div>
  );
}

/**
 * Module 8: human-facing browser for the knowledge engine (strategy catalog,
 * wiki, and news-derived-strategy concepts). Read-only — same query functions
 * the agent's own knowledge_engine_tool uses, so results match what the agent
 * sees when it reasons about a request.
 */
export function KnowledgeEngine() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("wiki");

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4">
      <section className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
        <div className="mb-3">
          <div className="text-sm font-semibold">{t("knowledgeEngine.title")}</div>
          <div className="text-xs text-muted-foreground">
            {t("knowledgeEngine.subtitle")}
          </div>
        </div>

        <div className="mb-4 flex gap-1 border-b border-border/60">
          {TABS.map((tabDef) => (
            <button
              key={tabDef.key}
              type="button"
              onClick={() => setTab(tabDef.key)}
              className={cn(
                "border-b-2 px-3 py-2 text-sm font-medium transition-colors",
                tab === tabDef.key
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {t(tabDef.label)}
            </button>
          ))}
        </div>

        {tab === "wiki" && <WikiTab />}
        {tab === "strategies" && <StrategiesTab />}
        {tab === "news" && <NewsDerivedTab />}
        {tab === "corpus" && <CorpusHealthTab />}
      </section>
    </div>
  );
}
