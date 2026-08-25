import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import {
  api,
  type AutonomousAgentInstance,
  type LivePositionGroup,
  type LivePositionSkipped,
} from "@/lib/api";
import { PnlForecastBandChart } from "@/components/board/PnlForecastBandChart";
import { cn } from "@/lib/utils";

// Live spot/IV re-price, not per-closed-trade data like AgentBoard.tsx's 60s wealth-curve
// poll — a shorter interval keeps this genuinely "live" without hammering the OpenAlgo
// position-book + Greeks calls compute_live_pop_for_agent makes per refresh.
const BOARD_POLL_MS = 20_000;

function fmtInr(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(0)}%`;
}

function LegsTable({ legs }: { legs: LivePositionGroup["legs"] }) {
  if (legs.length === 0) return null;
  return (
    <table className="mt-2 w-full text-[11px] text-muted-foreground">
      <thead>
        <tr className="border-b border-border/50 text-left">
          <th className="py-0.5 pr-2 font-medium">Side</th>
          <th className="py-0.5 pr-2 font-medium">Type</th>
          <th className="py-0.5 pr-2 font-medium">Strike</th>
          <th className="py-0.5 font-medium">Qty</th>
        </tr>
      </thead>
      <tbody>
        {legs.map((leg, i) => (
          <tr key={i} className="border-b border-border/30 last:border-0">
            <td className="py-0.5 pr-2">{leg.side}</td>
            <td className="py-0.5 pr-2">{leg.option_type}</td>
            <td className="py-0.5 pr-2">{leg.strike}</td>
            <td className="py-0.5">{leg.quantity}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PositionCard({ group }: { group: LivePositionGroup }) {
  const currentPnl = group.trajectory[0]?.pnl_inr;
  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-semibold">
            {group.underlying} · {group.expiry_days}d to expiry
          </div>
          <div className="text-xs text-muted-foreground">
            Live spot {fmtInr(group.live_spot)} · IV {group.live_iv_pct.toFixed(1)}% · POP{" "}
            {fmtPct(group.probability_of_profit)}
          </div>
        </div>
        <div
          className={cn(
            "rounded px-2 py-1 text-xs font-medium",
            (currentPnl ?? 0) >= 0
              ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
              : "bg-red-500/10 text-red-600 dark:text-red-400",
          )}
        >
          Current P&amp;L {fmtInr(currentPnl)}
        </div>
      </div>
      <PnlForecastBandChart band={group.pnl_forecast_band} />
      <LegsTable legs={group.legs} />
    </div>
  );
}

export function PositionsBoard() {
  const [agents, setAgents] = useState<AutonomousAgentInstance[]>([]);
  const [agentId, setAgentId] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [groups, setGroups] = useState<LivePositionGroup[]>([]);
  const [skipped, setSkipped] = useState<LivePositionSkipped[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listAutonomousAgents()
      .then((res) => {
        if (cancelled) return;
        setAgents(res.agents);
        if (res.agents.length > 0) setAgentId((prev) => prev || res.agents[0].id);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load agents.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const load = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    api
      .getAgentLivePositions(id)
      .then((res) => {
        setGroups(res.groups);
        setSkipped(res.skipped);
      })
      .catch(() => setError("Failed to load live positions for this agent."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!agentId) return;
    load(agentId);
    const timer = window.setInterval(() => load(agentId), BOARD_POLL_MS);
    return () => window.clearInterval(timer);
  }, [agentId, load]);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Positions</h1>
          <p className="text-sm text-muted-foreground">
            Live POP re-run and day-by-day P&amp;L forecast band per open position. Display
            only — no auto-trigger or exit decision happens here.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
            className="rounded-md border bg-background px-3 py-2 text-sm"
          >
            {agents.length === 0 ? <option value="">No agents</option> : null}
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name || a.id}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => agentId && load(agentId)}
            disabled={!agentId || loading}
            title="Refresh now"
            aria-label="Refresh now"
            className="rounded-md border bg-background p-2 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
          >
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          </button>
        </div>
      </div>

      {error ? (
        <div className="rounded-xl border border-red-500/40 bg-red-500/5 p-4 text-sm text-red-600 dark:text-red-400">
          {error}
        </div>
      ) : null}

      {!agentId ? (
        <div className="rounded-xl border border-dashed border-border/60 bg-muted/10 p-6 text-center text-sm text-muted-foreground">
          No autonomous agents exist yet.
        </div>
      ) : loading && groups.length === 0 && skipped.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border/60 bg-muted/10 p-6 text-center text-sm text-muted-foreground">
          Loading…
        </div>
      ) : groups.length === 0 && skipped.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border/60 bg-muted/10 p-6 text-center text-sm text-muted-foreground">
          No open option positions for this agent.
        </div>
      ) : (
        <div className="space-y-4">
          {groups.map((group) => (
            <PositionCard key={`${group.underlying}-${group.expiry_days}`} group={group} />
          ))}
          {skipped.map((row, i) => (
            <div
              key={`${row.underlying}-${i}`}
              className="rounded-lg border border-dashed border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-400"
            >
              {row.underlying}: not shown — {row.reason}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
