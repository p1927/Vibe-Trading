import { useCallback, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { SelectorCandidate, SelectorRankBy, SelectorResponseData } from "@/lib/options";
import { formatCurrency } from "@/lib/marketConfig";

const DEFAULT_TICKER = "NIFTY";

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "–";
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "–";
  return `${fmtNum(v * 100, 0)}%`;
}

function LegsTable({ legs }: { legs: SelectorCandidate["legs"] }) {
  const { t } = useTranslation();
  if (!Array.isArray(legs) || legs.length === 0) return null;
  return (
    <table className="mt-2 w-full text-[11px] text-muted-foreground">
      <thead>
        <tr className="border-b border-border/50 text-left">
          <th className="py-0.5 pr-2 font-medium">{t("options.india.selector.side", { defaultValue: "Side" })}</th>
          <th className="py-0.5 pr-2 font-medium">{t("options.india.selector.type", { defaultValue: "Type" })}</th>
          <th className="py-0.5 pr-2 font-medium">{t("options.india.selector.strike", { defaultValue: "Strike" })}</th>
          <th className="py-0.5 font-medium">{t("options.india.selector.qty", { defaultValue: "Qty" })}</th>
        </tr>
      </thead>
      <tbody>
        {legs.map((leg, i) => (
          <tr key={i} className="border-b border-border/30 last:border-0">
            <td className="py-0.5 pr-2">{String(leg.side ?? "–")}</td>
            <td className="py-0.5 pr-2">{String(leg.option_type ?? "–")}</td>
            <td className="py-0.5 pr-2">{fmtNum(leg.strike, 1)}</td>
            <td className="py-0.5">{leg.quantity ?? leg.lots ?? "–"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CandidateCard({ candidate }: { candidate: SelectorCandidate }) {
  const { t } = useTranslation();
  return (
    <div
      className={cn(
        "rounded-lg border p-3",
        candidate.meets_target
          ? "border-primary/40 bg-primary/5"
          : "border-border/60 bg-muted/20",
      )}
    >
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <div className="text-sm font-semibold">{candidate.name}</div>
        <span
          className={cn(
            "rounded px-1.5 py-0.5 text-[10px] font-medium",
            candidate.meets_target
              ? "bg-primary/10 text-primary"
              : "bg-muted text-muted-foreground",
          )}
        >
          {candidate.meets_target
            ? t("options.india.selector.meetsTarget", { defaultValue: "Meets target" })
            : t("options.india.selector.belowTarget", { defaultValue: "Below target" })}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-3">
        <div>
          {t("options.metrics.maxProfit")}{" "}
          <span className="font-medium text-foreground">
            {formatCurrency("india_equity", candidate.max_profit)}
          </span>
        </div>
        <div>
          {t("options.metrics.maxLoss")}{" "}
          <span className="font-medium text-foreground">
            {formatCurrency("india_equity", candidate.max_loss)}
          </span>
        </div>
        <div>
          {t("options.india.pop", { defaultValue: "PoP" })}{" "}
          <span className="font-medium text-foreground">
            {fmtPct(candidate.probability_of_profit)}
          </span>
        </div>
        <div>
          {t("options.india.selector.riskReward", { defaultValue: "Risk/Reward" })}{" "}
          <span className="font-medium text-foreground">{fmtNum(candidate.risk_reward_ratio)}</span>
        </div>
        <div>
          {t("options.india.selector.popPerRisk", { defaultValue: "PoP/Risk" })}{" "}
          <span className="font-medium text-foreground">{fmtNum(candidate.pop_per_risk, 4)}</span>
        </div>
        <div>
          {t("options.india.selector.expectedPnl", { defaultValue: "Expected P&L" })}{" "}
          <span className="font-medium text-foreground">
            {formatCurrency("india_equity", candidate.expected_pnl)}
          </span>
        </div>
      </div>
      {candidate.event_risks.length > 0 && (
        <div className="mt-2 text-[11px] text-amber-600 dark:text-amber-400">
          {t("options.india.selector.eventRisk", { defaultValue: "Event risk in window:" })}{" "}
          {candidate.event_risks.map((e) => e.label || e.category || JSON.stringify(e)).join(", ")}
        </div>
      )}
      {candidate.rationale && (
        <p className="mt-1.5 text-[11px] text-muted-foreground">{candidate.rationale}</p>
      )}
      <LegsTable legs={candidate.legs} />
    </div>
  );
}

/**
 * India-only module 5 panel: given a target profit, ranks candidate
 * strategies off the live chain by risk/reward or PoP-per-risk, and flags
 * upcoming events that could still flip the position into a loss.
 *
 * Deliberately its own panel, not merged into IndiaStrategyPanel — that one
 * shows the pipeline's own auto-ranked "best overall" suggestions
 * (GET /options/research); this one answers a different, user-driven
 * question ("least risk for *this* profit target") via GET
 * /options/india/selector, and every scored candidate is shown (not just
 * target-qualifying ones), matching the backend's never-filter contract.
 */
interface Props {
  /** Underlying picked from the page-level India picker — seeds the ticker
   * input as a convenience default. Does NOT auto-trigger a fetch: a real
   * run scores every generated candidate via Monte Carlo, so switching the
   * page-level underlying shouldn't fire an expensive request unasked. */
  underlying?: string;
}

export function IndiaSelectorPanel({ underlying }: Props) {
  const { t } = useTranslation();
  const [tickerInput, setTickerInput] = useState(underlying || DEFAULT_TICKER);
  const [targetProfitInput, setTargetProfitInput] = useState("1000");
  const [rankBy, setRankBy] = useState<SelectorRankBy>("risk_reward");
  const [data, setData] = useState<SelectorResponseData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (ticker: string, targetProfit: number, currentRankBy: SelectorRankBy) => {
      setLoading(true);
      setError(null);
      try {
        const res = await api.getIndiaOptionsSelector(ticker, targetProfit, {
          rankBy: currentRankBy,
        });
        if (!res.ok) {
          setError(res.error || t("options.india.requestFailed", { defaultValue: "Request failed" }));
          setData(null);
          return;
        }
        setData(res);
      } catch (e) {
        setError(
          e instanceof Error
            ? e.message
            : t("options.india.requestFailed", { defaultValue: "Request failed" }),
        );
        setData(null);
      } finally {
        setLoading(false);
      }
    },
    [t],
  );

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const ticker = tickerInput.trim().toUpperCase();
    const targetProfit = Number(targetProfitInput);
    if (!ticker || !Number.isFinite(targetProfit) || targetProfit <= 0) return;
    void load(ticker, targetProfit, rankBy);
  };

  return (
    <section className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-sm font-semibold">
            {t("options.india.selector.title", { defaultValue: "India: Least-Risk Selector" })}
          </div>
          <div className="text-xs text-muted-foreground">
            {t("options.india.selector.disclaimer", {
              defaultValue:
                "Ranks candidates by risk for a target profit — every scored candidate is shown, not just ones meeting the target.",
            })}
            {loading &&
              ` ${t("options.india.selector.latencyNote", {
                defaultValue: "Scoring the live chain can take up to a minute.",
              })}`}
          </div>
        </div>
        <form onSubmit={onSubmit} className="flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={tickerInput}
            onChange={(e) => setTickerInput(e.target.value)}
            placeholder={DEFAULT_TICKER}
            className="w-24 rounded-md border border-border/60 bg-background px-2 py-1.5 text-sm uppercase outline-none focus:ring-2 focus:ring-primary/40"
          />
          <input
            type="number"
            min={1}
            value={targetProfitInput}
            onChange={(e) => setTargetProfitInput(e.target.value)}
            placeholder={t("options.india.selector.targetProfit", { defaultValue: "Target profit" })}
            className="w-32 rounded-md border border-border/60 bg-background px-2 py-1.5 text-sm outline-none focus:ring-2 focus:ring-primary/40"
          />
          <select
            value={rankBy}
            onChange={(e) => setRankBy(e.target.value as SelectorRankBy)}
            className="rounded-md border border-border/60 bg-background px-2 py-1.5 text-sm outline-none focus:ring-2 focus:ring-primary/40"
          >
            <option value="risk_reward">
              {t("options.india.selector.riskReward", { defaultValue: "Risk/Reward" })}
            </option>
            <option value="pop_per_risk">
              {t("options.india.selector.popPerRisk", { defaultValue: "PoP/Risk" })}
            </option>
          </select>
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
                {formatCurrency("india_equity", data.underlying_ltp)}
              </span>
            </span>
            <span>
              {t("options.chain.expiration")}{" "}
              <span className="font-medium text-foreground">{data.expiry_date || "–"}</span>
            </span>
            <span>
              {t("options.india.selector.targetProfit", { defaultValue: "Target profit" })}{" "}
              <span className="font-medium text-foreground">
                {formatCurrency("india_equity", data.target_profit)}
              </span>
            </span>
          </div>
          {data.candidates.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("options.india.selector.noCandidates", { defaultValue: "No candidates returned." })}
            </p>
          ) : (
            <div className={cn("grid gap-2", "sm:grid-cols-2 lg:grid-cols-3")}>
              {data.candidates.map((candidate, i) => (
                <CandidateCard key={i} candidate={candidate} />
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
