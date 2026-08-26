import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError, type AdvisoryCandidate, type AdvisoryCandidatesResponse } from "@/lib/api";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { MiniPayoffChart } from "@/components/charts/MiniPayoffChart";
import { PnlForecastBandChart } from "@/components/board/PnlForecastBandChart";
import { cn } from "@/lib/utils";

// Mirrors AgentBoard.tsx's poll cadence — candidate scoring reads local cached research +
// a single ledger parquet read, cheap enough to poll but no reason to hammer it faster than
// the agent board does.
const BOARD_POLL_MS = 60_000;

function fmtInr(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(0)}%`;
}

function fmtRatio(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v.toFixed(2)}:1`;
}

function toneFor(v: number | null | undefined): "up" | "down" | "neutral" {
  if (v == null || !Number.isFinite(v)) return "neutral";
  return v >= 0 ? "up" : "down";
}

function ConfidenceBanner({ ticker, data }: { ticker: string; data: AdvisoryCandidatesResponse[string] }) {
  const c = data.confidence;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border bg-muted/10 px-3 py-2 text-xs text-muted-foreground">
      <span className="text-sm font-semibold text-foreground">{ticker}</span>
      <span>
        Direction <span className="font-medium text-foreground">{c.direction ?? "—"}</span>
      </span>
      <span>
        Confidence <span className="font-medium text-foreground">{fmtPct(c.confidence)}</span>
      </span>
      {c.is_stale && <span className="text-amber-600 dark:text-amber-400">stale prediction</span>}
    </div>
  );
}

function CandidateCard({
  ticker,
  candidate,
  onApprove,
}: {
  ticker: string;
  candidate: AdvisoryCandidate;
  onApprove: (ticker: string, candidate: AdvisoryCandidate) => void;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border p-3",
        candidate.net_ev_inr > 0 ? "border-primary/40 bg-primary/5" : "border-border/60 bg-muted/20",
      )}
    >
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <div className="text-sm font-semibold">{candidate.name}</div>
        <span
          className={cn(
            "rounded px-1.5 py-0.5 text-[10px] font-medium",
            toneFor(candidate.net_ev_inr) === "up"
              ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
              : "bg-red-500/10 text-red-600 dark:text-red-400",
          )}
        >
          {candidate.net_ev_inr > 0 ? "Net EV positive" : "Net EV negative"}
        </span>
      </div>
      {(candidate.payoff_samples?.length ?? 0) > 1 && (
        <MiniPayoffChart samples={candidate.payoff_samples} height={80} />
      )}
      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <div>
          Net EV <span className="font-medium text-foreground">{fmtInr(candidate.net_ev_inr)}</span>
        </div>
        <div>
          Max loss <span className="font-medium text-foreground">{fmtInr(candidate.max_loss_inr)}</span>
        </div>
        <div>
          Score <span className="font-medium text-foreground">{candidate.base_score.toFixed(2)}</span>
        </div>
        <div>
          Risk/Reward <span className="font-medium text-foreground">{fmtRatio(candidate.risk_reward_ratio)}</span>
        </div>
      </div>
      {(candidate.trajectory_band?.length ?? 0) > 1 && (
        <div className="mt-2">
          <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">
            Predicted trajectory
          </div>
          <PnlForecastBandChart band={candidate.trajectory_band} height={110} />
        </div>
      )}
      <button
        type="button"
        onClick={() => onApprove(ticker, candidate)}
        className="mt-2.5 w-full rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition-opacity hover:opacity-90"
      >
        Approve
      </button>
    </div>
  );
}

interface PendingApproval {
  ticker: string;
  candidate: AdvisoryCandidate;
  widgetId: string;
  orders: Record<string, unknown>[];
}

export function AdvisoryBoard() {
  const [data, setData] = useState<AdvisoryCandidatesResponse>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [preparing, setPreparing] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [executing, setExecuting] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .getAdvisoryCandidates()
      .then(setData)
      .catch(() => setError("Failed to load live candidates."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, BOARD_POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const handleApprove = useCallback(async (ticker: string, candidate: AdvisoryCandidate) => {
    setPreparing(candidate.name);
    try {
      const res = await api.prepareAdvisoryWidget({ ticker, strategyName: candidate.name });
      setPending({ ticker, candidate, widgetId: res.widget_id, orders: res.orders });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to prepare order");
    } finally {
      setPreparing(null);
    }
  }, []);

  const handleConfirm = useCallback(async () => {
    if (!pending) return;
    setExecuting(true);
    try {
      const result = await api.executeTradeBasket({ widget_id: pending.widgetId, orders: pending.orders });
      const mode = result.execution_mode === "paper" ? " (paper)" : "";
      toast.success((result.message || "Basket order submitted") + mode);
    } catch (err) {
      if (err instanceof ApiError && err.status === 0) {
        toast.error(
          "No response from the server — the order status is unknown. Check your positions before retrying.",
          { duration: 10000 },
        );
      } else {
        toast.error(err instanceof Error ? err.message : "Execution failed");
      }
    } finally {
      setExecuting(false);
      setPending(null);
    }
  }, [pending]);

  const tickers = Object.keys(data);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Advisory Board</h1>
          <p className="text-sm text-muted-foreground">
            Live market prediction state and which option strategies would currently be
            profitable — approve to place a real order. No agent involved.
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          title="Refresh now"
          aria-label="Refresh now"
          className="rounded-md border bg-background p-2 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
        >
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
        </button>
      </div>

      {error ? (
        <div className="rounded-xl border border-red-500/40 bg-red-500/5 p-4 text-sm text-red-600 dark:text-red-400">
          {error}
        </div>
      ) : null}

      {loading && tickers.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border/60 bg-muted/10 p-6 text-center text-sm text-muted-foreground">
          Loading…
        </div>
      ) : tickers.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border/60 bg-muted/10 p-6 text-center text-sm text-muted-foreground">
          No watchlist tickers configured (AUTONOMOUS_AGENT_TRADING_WATCHLIST).
        </div>
      ) : (
        tickers.map((ticker) => {
          const entry = data[ticker];
          return (
            <section key={ticker} className="space-y-2">
              <ConfidenceBanner ticker={ticker} data={entry} />
              {entry.candidates.length === 0 ? (
                <p className="px-1 text-sm text-muted-foreground">
                  No qualifying candidates right now.
                </p>
              ) : (
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {entry.candidates.map((candidate) => (
                    <CandidateCard
                      key={candidate.name}
                      ticker={ticker}
                      candidate={candidate}
                      onApprove={handleApprove}
                    />
                  ))}
                </div>
              )}
            </section>
          );
        })
      )}

      {preparing ? (
        <div className="fixed bottom-4 right-4 flex items-center gap-2 rounded-md border bg-card px-3 py-2 text-xs text-muted-foreground shadow-sm">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Preparing {preparing}…
        </div>
      ) : null}

      <ConfirmDialog
        open={pending != null}
        title="Place order"
        description={
          pending
            ? `Place ${pending.orders.length} leg(s) for ${pending.ticker} — ${pending.candidate.name}. This submits a real order.`
            : undefined
        }
        confirmLabel={executing ? "Submitting…" : "Approve & execute"}
        cancelLabel="Cancel"
        onConfirm={handleConfirm}
        onCancel={() => setPending(null)}
      >
        {pending ? (
          <div className="text-xs text-muted-foreground">
            Net EV {fmtInr(pending.candidate.net_ev_inr)} · Max loss {fmtInr(pending.candidate.max_loss_inr)}
          </div>
        ) : null}
      </ConfirmDialog>
    </div>
  );
}
