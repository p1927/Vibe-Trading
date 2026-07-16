import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { api, type IndexQuantReviewReport } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  ticker?: string;
  horizonDays?: number;
}

export function QuantReviewPanel({ ticker = "NIFTY", horizonDays = 14 }: Props) {
  const [review, setReview] = useState<IndexQuantReviewReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (refresh = false) => {
      setLoading(true);
      setError(null);
      try {
        const res = await api.getIndexQuantReview(ticker, refresh, horizonDays);
        setReview(res.review ?? null);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Quant review load failed");
      } finally {
        setLoading(false);
      }
    },
    [ticker, horizonDays]
  );

  useEffect(() => {
    void load(false);
  }, [load]);

  const directionTone = (dir?: string) => {
    const d = (dir || "").toLowerCase();
    if (d.includes("bull")) return "text-emerald-600 dark:text-emerald-400";
    if (d.includes("bear")) return "text-red-600 dark:text-red-400";
    return "text-muted-foreground";
  };

  return (
    <section className="rounded-xl border bg-card p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">Quant review</h2>
          <p className="text-[11px] text-muted-foreground">
            Second opinion — separate from Ridge headline forecast
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load(true)}
          disabled={loading}
          className="inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] hover:bg-muted"
        >
          {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
          Refresh
        </button>
      </div>

      {error ? (
        <div className="flex items-center gap-2 text-[12px] text-amber-700 dark:text-amber-300">
          <AlertTriangle className="h-4 w-4" />
          {error}
        </div>
      ) : null}

      {!review && !loading && !error ? (
        <p className="text-[12px] text-muted-foreground">No quant review yet. Run refresh.</p>
      ) : null}

      {review ? (
        <div className="space-y-3 text-[12px]">
          <div className="flex flex-wrap gap-3">
            <div>
              <span className="text-muted-foreground">Model forecast: </span>
              <span className="font-medium">{review.model_prediction_view ?? "—"}</span>
              {review.model_expected_return_pct != null ? (
                <span className="ml-1 tabular-nums">
                  ({Number(review.model_expected_return_pct).toFixed(2)}%)
                </span>
              ) : null}
            </div>
            <div>
              <span className="text-muted-foreground">TA consensus: </span>
              <span className={cn("font-medium uppercase", directionTone(review.ta_consensus?.direction))}>
                {review.ta_consensus?.direction ?? "neutral"}
              </span>
            </div>
            {review.active_strategy_profile ? (
              <div>
                <span className="text-muted-foreground">Profile: </span>
                <span className="font-medium">{review.active_strategy_profile.replace(/_/g, " ")}</span>
              </div>
            ) : null}
          </div>

          {review.technical_interpretation ? (
            <p className="leading-relaxed text-muted-foreground">{review.technical_interpretation}</p>
          ) : null}

          {(review.disagreements_with_forecast?.length ?? 0) > 0 ? (
            <div>
              <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-400">
                Disagreements with model
              </h3>
              <ul className="list-disc space-y-1 pl-4 text-muted-foreground">
                {review.disagreements_with_forecast!.map((row, i) => (
                  <li key={i}>{row.detail}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {(review.surprises?.length ?? 0) > 0 ? (
            <div>
              <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide">Surprises</h3>
              <ul className="list-disc space-y-1 pl-4 text-muted-foreground">
                {review.surprises!.map((row, i) => (
                  <li key={i}>{row.message}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {review.disclaimer ? (
            <p className="text-[10px] italic text-muted-foreground">{review.disclaimer}</p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
