import { useCallback, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { IndiaOptionsSource, PopOverlayResponse, PopOverlayRow } from "@/lib/options";
import { formatCurrency } from "@/lib/marketConfig";
import { PopOverlayChart } from "@/components/charts/PopOverlayChart";

const DEFAULT_TICKER = "NIFTY";
const MIN_HORIZON = 1;
const MAX_HORIZON = 60;

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "–";
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "–";
  return `${fmtNum(v * 100, 0)}%`;
}

function ChainRow({ row }: { row: PopOverlayRow }) {
  const pop = row.pop;
  return (
    <tr className="border-b border-border/30 last:border-0">
      <td className="py-1 pr-2 tabular-nums">{fmtNum(row.strike, 1)}</td>
      <td className="py-1 pr-2">
        <span
          className={cn(
            "rounded px-1 py-0.5 text-[10px] font-medium",
            row.option_type === "CE"
              ? "bg-success/10 text-success"
              : "bg-danger/10 text-danger",
          )}
        >
          {row.option_type}
        </span>
      </td>
      <td className="py-1 pr-2 tabular-nums">{fmtNum(row.last_price)}</td>
      <td className="py-1 pr-2 tabular-nums">{fmtNum(row.bid)}</td>
      <td className="py-1 pr-2 tabular-nums">{fmtNum(row.ask)}</td>
      <td className="py-1 pr-2 tabular-nums font-medium">
        {pop ? fmtPct(pop.probability_of_profit) : "–"}
      </td>
      <td className="py-1 pr-2 tabular-nums">{pop ? fmtNum(pop.expected_pnl) : "–"}</td>
      <td className="py-1 pr-2 tabular-nums text-muted-foreground">
        {pop ? `${fmtNum(pop.pnl_p10)} / ${fmtNum(pop.pnl_p50)} / ${fmtNum(pop.pnl_p90)}` : (row.pop_skip_reason ?? "–")}
      </td>
    </tr>
  );
}

/**
 * India-only module 4 panel: the full option chain for a chosen ticker,
 * with a time-horizon selector and each strike's Monte Carlo
 * probability-of-profit overlaid — the specific UI the original user
 * request asked for (distinct from `IndiaSelectorPanel`'s filtered,
 * risk-ranked candidate table).
 */
interface Props {
  underlying?: string;
  source: IndiaOptionsSource;
}

export function IndiaPopOverlayPanel({ underlying, source }: Props) {
  const { t } = useTranslation();
  const [tickerInput, setTickerInput] = useState(underlying || DEFAULT_TICKER);
  const [horizonDays, setHorizonDays] = useState(7);
  const [data, setData] = useState<PopOverlayResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (ticker: string, horizon: number) => {
      setLoading(true);
      setError(null);
      try {
        const res = await api.getIndiaOptionsPopOverlay(ticker, { horizonDays: horizon, source });
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
    [source, t],
  );

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const ticker = tickerInput.trim().toUpperCase();
    if (!ticker || !Number.isFinite(horizonDays) || horizonDays < MIN_HORIZON) return;
    void load(ticker, horizonDays);
  };

  return (
    <section className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-sm font-semibold">
            {t("options.india.popOverlay.title", { defaultValue: "India: Chain Probability Overlay" })}
          </div>
          <div className="text-xs text-muted-foreground">
            {t("options.india.popOverlay.disclaimer", {
              defaultValue:
                "Full chain scored by Monte Carlo for the selected horizon — pick a time horizon, see PoP for every strike.",
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
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            {t("options.india.popOverlay.horizon", { defaultValue: "Horizon (days)" })}
            <input
              type="number"
              min={MIN_HORIZON}
              max={MAX_HORIZON}
              value={horizonDays}
              onChange={(e) => setHorizonDays(Number(e.target.value))}
              className="w-16 rounded-md border border-border/60 bg-background px-2 py-1.5 text-sm outline-none focus:ring-2 focus:ring-primary/40"
            />
          </label>
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
              {t("options.india.popOverlay.horizon", { defaultValue: "Horizon (days)" })}{" "}
              <span className="font-medium text-foreground">{data.horizon_days}</span>
            </span>
            <span>
              {t("options.india.popOverlay.forecastP50", { defaultValue: "Forecast p50 (%)" })}{" "}
              <span className="font-medium text-foreground">{fmtNum(data.forecast_quantiles?.p50)}</span>
            </span>
          </div>

          <PopOverlayChart rows={data.overlay} underlyingLtp={data.underlying_ltp} />

          {data.event_risks.length > 0 && (
            <div className="mt-2 mb-2 text-[11px] text-amber-600 dark:text-amber-400">
              {t("options.india.selector.eventRisk", { defaultValue: "Event risk in window:" })}{" "}
              {data.event_risks.map((e) => e.label || e.category || JSON.stringify(e)).join(", ")}
            </div>
          )}

          <div className="mt-3 max-h-96 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-card">
                <tr className="border-b border-border/50 text-left text-muted-foreground">
                  <th className="py-1 pr-2 font-medium">{t("options.chain.strike", { defaultValue: "Strike" })}</th>
                  <th className="py-1 pr-2 font-medium">{t("options.india.selector.type", { defaultValue: "Type" })}</th>
                  <th className="py-1 pr-2 font-medium">{t("options.chain.last", { defaultValue: "Last" })}</th>
                  <th className="py-1 pr-2 font-medium">{t("options.chain.bid", { defaultValue: "Bid" })}</th>
                  <th className="py-1 pr-2 font-medium">{t("options.chain.ask", { defaultValue: "Ask" })}</th>
                  <th className="py-1 pr-2 font-medium">{t("options.india.pop", { defaultValue: "PoP" })}</th>
                  <th className="py-1 pr-2 font-medium">
                    {t("options.india.selector.expectedPnl", { defaultValue: "Expected P&L" })}
                  </th>
                  <th className="py-1 pr-2 font-medium">
                    {t("options.india.popOverlay.pnlBand", { defaultValue: "PnL p10 / p50 / p90" })}
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.overlay.map((row, i) => (
                  <ChainRow key={`${row.option_type}-${row.strike}-${i}`} row={row} />
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
