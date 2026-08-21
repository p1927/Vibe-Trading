import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { IndiaOptionsResearchData, RankedOptionStrategy } from "@/lib/options";
import { formatCurrency } from "@/lib/marketConfig";

const DEFAULT_TICKER = "NIFTY";

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "–";
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function StrategyCard({ strategy }: { strategy: RankedOptionStrategy }) {
  const { t } = useTranslation();
  const legs = Array.isArray(strategy.legs) ? strategy.legs : [];
  return (
    <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <div className="text-sm font-semibold">
          {strategy.name ?? t("options.india.strategyFallbackName", { defaultValue: "Strategy" })}
        </div>
        {strategy.tier && (
          <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
            {strategy.tier}
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-4">
        <div>
          {t("options.india.score", { defaultValue: "Score" })}{" "}
          <span className="font-medium text-foreground">{fmtNum(strategy.score)}</span>
        </div>
        <div>
          {t("options.india.pop", { defaultValue: "PoP" })}{" "}
          <span className="font-medium text-foreground">
            {strategy.pop !== undefined ? `${fmtNum((strategy.pop ?? 0) * 100, 0)}%` : "–"}
          </span>
        </div>
        <div>
          {t("options.metrics.maxProfit")}{" "}
          <span className="font-medium text-foreground">
            {formatCurrency("india_equity", strategy.max_profit ?? null)}
          </span>
        </div>
        <div>
          {t("options.metrics.maxLoss")}{" "}
          <span className="font-medium text-foreground">
            {formatCurrency("india_equity", strategy.max_loss ?? null)}
          </span>
        </div>
      </div>
      {legs.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5 text-[11px] text-muted-foreground">
          {legs.map((leg, i) => (
            <span key={i} className="rounded border border-border/50 px-1.5 py-0.5">
              {JSON.stringify(leg)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * India-only auto-ranked strategy suggestions from `GET /options/research`
 * (`dataflows/options_research`'s candidate-generation + ranking pipeline).
 *
 * Deliberately its own panel rather than feeding StrategyBuilder/the payoff
 * chart: a ranked strategy is a different product from a user-built payoff
 * (auto-selected, possibly multi-leg, scored) — see the backend tool's docs.
 */
interface Props {
  /** Underlying picked from the page-level India picker — seeds the ticker
   * input as a convenience default. Does NOT auto-trigger a fetch: a real
   * run takes ~156s end to end, so switching the page-level underlying
   * shouldn't fire an expensive request the user didn't explicitly ask for. */
  underlying?: string;
}

export function IndiaStrategyPanel({ underlying }: Props) {
  const { t } = useTranslation();
  const [tickerInput, setTickerInput] = useState(underlying || DEFAULT_TICKER);
  const [data, setData] = useState<IndiaOptionsResearchData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);

  useEffect(() => {
    if (underlying) setTickerInput(underlying);
  }, [underlying]);

  const load = useCallback(async (ticker: string) => {
    const gen = ++generation.current;
    setLoading(true);
    setError(null);
    try {
      const res = await api.getIndiaOptionsResearch(ticker);
      if (generation.current !== gen) return;
      if (!res.ok || !res.data) {
        setError(res.error || t("options.india.requestFailed", { defaultValue: "Request failed" }));
        setData(null);
        return;
      }
      setData(res.data);
    } catch (e) {
      if (generation.current !== gen) return;
      setError(
        e instanceof Error
          ? e.message
          : t("options.india.requestFailed", { defaultValue: "Request failed" }),
      );
      setData(null);
    } finally {
      if (generation.current === gen) setLoading(false);
    }
  }, [t]);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const ticker = tickerInput.trim().toUpperCase();
    if (!ticker) return;
    void load(ticker);
  };

  return (
    <section className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-sm font-semibold">
            {t("options.india.title", { defaultValue: "India: Ranked Strategy Suggestions" })}
          </div>
          <div className="text-xs text-muted-foreground">
            {t("options.india.disclaimer", {
              defaultValue:
                "Auto-generated and scored — not a substitute for the manual payoff builder above.",
            })}
            {loading &&
              ` ${t("options.india.latencyNote", {
                defaultValue: "Fetching live data across several sources can take up to ~3 minutes.",
              })}`}
          </div>
        </div>
        <form onSubmit={onSubmit} className="flex items-center gap-2">
          <input
            type="text"
            value={tickerInput}
            onChange={(e) => setTickerInput(e.target.value)}
            placeholder={DEFAULT_TICKER}
            className="w-28 rounded-md border border-border/60 bg-background px-2 py-1.5 text-sm uppercase outline-none focus:ring-2 focus:ring-primary/40"
          />
          <button
            type="submit"
            disabled={loading}
            className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : t("options.india.load", { defaultValue: "Load" })}
          </button>
        </form>
      </div>

      {error && (
        <div className="mb-3 rounded border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
          {error}
        </div>
      )}

      {data && !error && (
        <>
          <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>
              {t("options.india.spot", { defaultValue: "Spot" })}{" "}
              <span className="font-medium text-foreground">
                {formatCurrency("india_equity", data.spot)}
              </span>
            </span>
            <span>
              {t("options.chain.atm")}{" "}
              <span className="font-medium text-foreground">
                {formatCurrency("india_equity", data.atm_strike)}
              </span>
            </span>
            <span>
              {t("options.india.pcr", { defaultValue: "PCR" })}{" "}
              <span className="font-medium text-foreground">{fmtNum(data.pcr)}</span>
            </span>
            <span>
              {t("options.chain.expiration")}{" "}
              <span className="font-medium text-foreground">{data.expiry || "–"}</span>
            </span>
          </div>
          {data.ranked_strategies.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("options.india.noStrategies", { defaultValue: "No ranked strategies returned." })}
            </p>
          ) : (
            <div className={cn("grid gap-2", "sm:grid-cols-2 lg:grid-cols-3")}>
              {data.ranked_strategies.map((strategy, i) => (
                <StrategyCard key={i} strategy={strategy} />
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
