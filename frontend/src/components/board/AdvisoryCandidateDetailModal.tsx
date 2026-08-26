import { useEffect, useState } from "react";
import { Loader2, X } from "lucide-react";
import { api } from "@/lib/api";
import type { AdvisoryCandidate, AdvisoryCandidateDetailResponse } from "@/lib/api";
import { fmtInr, fmtPct, fmtPctScale, fmtRatio } from "@/lib/advisoryFormat";
import { OptionsPayoffChart } from "@/components/charts/OptionsPayoffChart";
import { PnlForecastBandChart } from "@/components/board/PnlForecastBandChart";

interface Props {
  ticker: string;
  candidate: AdvisoryCandidate;
  approving: boolean;
  onClose: () => void;
  onApprove: () => void;
}

/**
 * Pre-approval detail view for one Advisory board candidate
 * (2026-08-27-advisory-candidate-detail-view) — the full payoff diagram, predicted
 * price/PnL trajectory, risk/reward, and an explanation of how the recommendation was
 * calculated, so the user can evaluate it before clicking Approve. The payoff curve,
 * risk/reward, and trajectory band all come straight from `candidate` (already fetched
 * by the board's poll, via `score_ranked_strategies`) — only the prediction explanation
 * and leg breakdown are fetched fresh here, from `GET /board/advisory/candidate-detail`.
 */
export function AdvisoryCandidateDetailModal({ ticker, candidate, approving, onClose, onApprove }: Props) {
  const [detail, setDetail] = useState<AdvisoryCandidateDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getAdvisoryCandidateDetail(ticker, candidate.name)
      .then((res) => {
        if (cancelled) return;
        if (!res.ok) {
          setError(res.error || "Failed to load detail");
          return;
        }
        setDetail(res);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load detail");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ticker, candidate.name]);

  const curve = {
    spot: candidate.payoff_samples.map((p) => p.spot),
    pnl: candidate.payoff_samples.map((p) => p.pnl),
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-xl border border-border/60 bg-card p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">{candidate.name}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mb-4 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs text-muted-foreground sm:grid-cols-4">
          <div>
            Net EV <div className="font-medium text-foreground">{fmtInr(candidate.net_ev_inr)}</div>
          </div>
          <div>
            Max profit <div className="font-medium text-foreground">{fmtInr(candidate.max_profit_inr)}</div>
          </div>
          <div>
            Max loss <div className="font-medium text-foreground">{fmtInr(candidate.max_loss_inr)}</div>
          </div>
          <div>
            Risk/Reward <div className="font-medium text-foreground">{fmtRatio(candidate.risk_reward_ratio)}</div>
          </div>
        </div>

        {candidate.payoff_samples.length > 1 && (
          <section className="mb-4">
            <div className="mb-1 text-xs font-semibold text-muted-foreground">Payoff diagram</div>
            <OptionsPayoffChart curve={curve} entrySpot={detail?.spot ?? 0} breakevens={[]} height={240} />
          </section>
        )}

        {candidate.trajectory_band.length > 1 && (
          <section className="mb-4">
            <div className="mb-1 text-xs font-semibold text-muted-foreground">Predicted trajectory</div>
            <PnlForecastBandChart band={candidate.trajectory_band} height={200} />
          </section>
        )}

        <section className="mb-4">
          <div className="mb-1 text-xs font-semibold text-muted-foreground">How this was calculated</div>
          {loading && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" /> Loading…
            </div>
          )}
          {error && <div className="text-xs text-danger">{error}</div>}
          {detail && !loading && !error && (
            <div className="space-y-2 text-xs text-muted-foreground">
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                <span>
                  View <span className="font-medium text-foreground">{detail.prediction.view ?? "—"}</span>
                </span>
                <span>
                  IV regime{" "}
                  <span className="font-medium text-foreground">{detail.prediction.iv_regime ?? "—"}</span>
                </span>
                <span>
                  Expected move{" "}
                  <span className="font-medium text-foreground">
                    {fmtPctScale(detail.prediction.expected_move_pct)}
                  </span>
                </span>
                <span>
                  Confidence{" "}
                  <span className="font-medium text-foreground">{fmtPct(detail.prediction.confidence)}</span>
                </span>
              </div>
              {detail.rationale && <p>{detail.rationale}</p>}
              {!detail.found && (
                <p className="text-amber-600 dark:text-amber-400">
                  Leg breakdown temporarily unavailable — the underlying research doc may have refreshed
                  since this candidate was last scored.
                </p>
              )}
            </div>
          )}
        </section>

        {detail && detail.legs.length > 0 && (
          <section className="mb-4">
            <div className="mb-1 text-xs font-semibold text-muted-foreground">Legs</div>
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border/50 text-left text-muted-foreground">
                  <th className="py-1 pr-2 font-medium">Side</th>
                  <th className="py-1 pr-2 font-medium">Type</th>
                  <th className="py-1 pr-2 font-medium">Strike</th>
                  <th className="py-1 font-medium">Price</th>
                </tr>
              </thead>
              <tbody>
                {detail.legs.map((leg, i) => (
                  <tr key={i} className="border-b border-border/30 last:border-0">
                    <td className="py-1 pr-2">{String(leg.side ?? "—")}</td>
                    <td className="py-1 pr-2">{String(leg.option_type ?? "—")}</td>
                    <td className="py-1 pr-2">{leg.strike ?? "—"}</td>
                    <td className="py-1">{leg.price ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        <button
          type="button"
          onClick={onApprove}
          disabled={approving}
          className="mt-1 w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {approving ? "Preparing…" : "Approve"}
        </button>
      </div>
    </div>
  );
}
