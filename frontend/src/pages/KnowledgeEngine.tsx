import { useCallback, useEffect, useState } from "react";
import { Loader2, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, ApiError } from "@/lib/api";
import type { NewsDerivedConcept, StrategyEntry, WikiEntry } from "@/lib/knowledgeEngine";

type Tab = "wiki" | "strategies" | "news";

const TABS: { key: Tab; label: string }[] = [
  { key: "wiki", label: "Wiki" },
  { key: "strategies", label: "Strategy Catalog" },
  { key: "news", label: "News-Derived Concepts" },
];

function ScoreBadge({ score }: { score: number }) {
  return (
    <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
      {score.toFixed(2)}
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
  const [text, setText] = useState("");
  const [results, setResults] = useState<WikiEntry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openSlug, setOpenSlug] = useState<string | null>(null);
  const [pageContent, setPageContent] = useState<Record<string, string>>({});
  const [pageLoading, setPageLoading] = useState<string | null>(null);

  const load = useCallback(async (q: string) => {
    setLoading(true);
    try {
      const res = await api.queryKnowledgeWiki({ text: q || undefined, limit: 20 });
      setError(null);
      setResults(res.results);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => void load(text), 300);
    return () => clearTimeout(t);
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
        setPageContent((prev) => ({ ...prev, [slug]: page.found ? page.content ?? "" : "Page not found." }));
      } catch (e) {
        setPageContent((prev) => ({
          ...prev,
          [slug]: e instanceof ApiError ? `Failed to load: ${e.message}` : "Failed to load.",
        }));
      } finally {
        setPageLoading(null);
      }
    }
  };

  return (
    <div className="space-y-3">
      <SearchBar value={text} onChange={setText} placeholder="Search the wiki (concepts, terms, playbooks)…" />
      {error && <div className="rounded border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{error}</div>}
      {loading && !results && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
        </div>
      )}
      {!loading && results && results.length === 0 && (
        <p className="text-sm text-muted-foreground">No wiki entries match.</p>
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
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading page…
                  </div>
                ) : (
                  <pre className="whitespace-pre-wrap font-sans text-xs leading-relaxed text-foreground">
                    {pageContent[entry.slug]}
                  </pre>
                )}
                {entry.sources?.length ? (
                  <div className="mt-2 text-[10px] text-muted-foreground">
                    Sources: {entry.sources.join(", ")}
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
  return (
    <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="text-sm font-semibold">{strategy.label ?? strategy.key}</div>
        <ScoreBadge score={strategy.score} />
      </div>
      {strategy.logic && <p className="mt-1 text-xs text-muted-foreground">{strategy.logic}</p>}
      {strategy.when && (
        <p className="mt-1 text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground">When: </span>
          {strategy.when}
        </p>
      )}
      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-muted-foreground sm:grid-cols-3">
        {strategy.market_view && (
          <div>
            Market view <span className="font-medium text-foreground">{strategy.market_view}</span>
          </div>
        )}
        {strategy.risk_profile && (
          <div>
            Risk <span className="font-medium text-foreground">{strategy.risk_profile}</span>
          </div>
        )}
        {strategy.horizon_fit && (
          <div>
            Horizon <span className="font-medium text-foreground">{strategy.horizon_fit}</span>
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
  const [marketView, setMarketView] = useState("");
  const [riskProfile, setRiskProfile] = useState("");
  const [results, setResults] = useState<StrategyEntry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.queryKnowledgeStrategies({
        marketView: marketView || undefined,
        riskProfile: riskProfile || undefined,
        limit: 20,
      });
      setError(null);
      setResults(res.results);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }, [marketView, riskProfile]);

  useEffect(() => {
    const t = setTimeout(() => void load(), 300);
    return () => clearTimeout(t);
  }, [load]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <input
          type="text"
          value={marketView}
          onChange={(e) => setMarketView(e.target.value)}
          placeholder="Market view (e.g. bullish)"
          className="w-48 rounded-md border border-border/60 bg-background px-3 py-2 text-sm outline-none focus:border-primary"
        />
        <input
          type="text"
          value={riskProfile}
          onChange={(e) => setRiskProfile(e.target.value)}
          placeholder="Risk profile (e.g. conservative)"
          className="w-48 rounded-md border border-border/60 bg-background px-3 py-2 text-sm outline-none focus:border-primary"
        />
      </div>
      {error && <div className="rounded border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{error}</div>}
      {loading && !results && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
        </div>
      )}
      {!loading && results && results.length === 0 && (
        <p className="text-sm text-muted-foreground">No strategies match.</p>
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
  return (
    <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="text-sm font-semibold">
          {concept.tactic_kind ?? concept.instrument ?? `Concept ${index + 1}`}
        </div>
        <ScoreBadge score={concept.score} />
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{concept.text}</p>
      {concept.trigger_context && (
        <p className="mt-1 text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground">Trigger: </span>
          {concept.trigger_context}
        </p>
      )}
      <TagList tags={concept.tags} />
      {concept.source_citation && (
        <div className="mt-2 text-[10px] text-muted-foreground">Source: {concept.source_citation}</div>
      )}
    </div>
  );
}

function NewsDerivedTab() {
  const [text, setText] = useState("");
  const [results, setResults] = useState<NewsDerivedConcept[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (q: string) => {
    setLoading(true);
    try {
      const res = await api.queryKnowledgeNewsDerivedStrategies({ text: q || undefined, limit: 20 });
      setError(null);
      setResults(res.results);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => void load(text), 300);
    return () => clearTimeout(t);
  }, [text, load]);

  return (
    <div className="space-y-3">
      <SearchBar value={text} onChange={setText} placeholder="Search news-derived concepts…" />
      {error && <div className="rounded border border-danger/30 bg-danger/5 p-3 text-sm text-danger">{error}</div>}
      {loading && !results && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
        </div>
      )}
      {!loading && results && results.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No news-derived concepts yet — these are extracted and verified from live news as they
          arrive.
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

/**
 * Module 8: human-facing browser for the knowledge engine (strategy catalog,
 * wiki, and news-derived-strategy concepts). Read-only — same query functions
 * the agent's own knowledge_engine_tool uses, so results match what the agent
 * sees when it reasons about a request.
 */
export function KnowledgeEngine() {
  const [tab, setTab] = useState<Tab>("wiki");

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4">
      <section className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
        <div className="mb-3">
          <div className="text-sm font-semibold">Knowledge Engine</div>
          <div className="text-xs text-muted-foreground">
            Browse the strategy catalog, wiki, and verified news-derived concepts — the same
            knowledge base the agent draws on.
          </div>
        </div>

        <div className="mb-4 flex gap-1 border-b border-border/60">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              className={cn(
                "border-b-2 px-3 py-2 text-sm font-medium transition-colors",
                tab === t.key
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {t.label}
            </button>
          ))}
        </div>

        {tab === "wiki" && <WikiTab />}
        {tab === "strategies" && <StrategiesTab />}
        {tab === "news" && <NewsDerivedTab />}
      </section>
    </div>
  );
}
