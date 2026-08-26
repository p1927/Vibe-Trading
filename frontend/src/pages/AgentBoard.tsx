import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import {
  api,
  type AgentBoardHindsightSummary,
  type AgentBoardSummary,
  type AgentBoardWealthCurvePoint,
  type AgentBoardHindsightCurve,
  type AutonomousAgentInstance,
  type ModelVersionTimelineEntry,
  type PendingWeightProposal,
} from "@/lib/api";
import { AgentPnlCurveChart, type PnlSeries } from "@/components/board/AgentPnlCurveChart";
import { cn } from "@/lib/utils";

const CANDIDATE_COLORS = ["#3b82f6", "#8b5cf6", "#f59e0b", "#14b8a6", "#ec4899", "#6366f1"];

// This board's data changes per-closed-trade, not per-tick like Autonomous.tsx's runtime
// strip (RUNTIME_POLL_MS=15s there) — a slower interval is enough to avoid staleness
// without polling 5 endpoints needlessly often.
const BOARD_POLL_MS = 60_000;

function fmtInr(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(0)}%`;
}

function StatCard({ label, value, tone }: { label: string; value: string; tone?: "up" | "down" | "neutral" }) {
  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div
        className={cn(
          "mt-1 text-xl font-semibold",
          tone === "up" && "text-emerald-600 dark:text-emerald-400",
          tone === "down" && "text-red-600 dark:text-red-400",
        )}
      >
        {value}
      </div>
    </div>
  );
}

function toneFor(v: number | null | undefined): "up" | "down" | "neutral" {
  if (v == null || !Number.isFinite(v)) return "neutral";
  return v >= 0 ? "up" : "down";
}

export function AgentBoard() {
  const [agents, setAgents] = useState<AutonomousAgentInstance[]>([]);
  const [agentId, setAgentId] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState<AgentBoardSummary | null>(null);
  const [wealthPoints, setWealthPoints] = useState<AgentBoardWealthCurvePoint[]>([]);
  const [hindsight, setHindsight] = useState<AgentBoardHindsightSummary | null>(null);
  const [hindsightCurves, setHindsightCurves] = useState<AgentBoardHindsightCurve[]>([]);
  const [timeline, setTimeline] = useState<ModelVersionTimelineEntry[]>([]);
  const [proposals, setProposals] = useState<PendingWeightProposal[]>([]);
  const [appliedProposals, setAppliedProposals] = useState<PendingWeightProposal[]>([]);
  const [applyingId, setApplyingId] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [revertingId, setRevertingId] = useState<string | null>(null);
  const [proposalError, setProposalError] = useState<string | null>(null);
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

  const loadBoard = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    Promise.all([
      api.getAgentBoardSummary(id),
      api.getAgentBoardWealthCurve(id),
      api.getAgentBoardHindsight(id),
      api.getAgentBoardHindsightCurves(id),
      api.getModelVersionTimeline({ agentId: id }),
    ])
      .then(([s, w, h, hc, t]) => {
        setSummary(s);
        setWealthPoints(w.points);
        setHindsight(h);
        setHindsightCurves(hc.curves);
        setTimeline(t.timeline);
      })
      .catch(() => setError("Failed to load board data for this agent."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!agentId) return;
    loadBoard(agentId);
    const timer = window.setInterval(() => loadBoard(agentId), BOARD_POLL_MS);
    return () => window.clearInterval(timer);
  }, [agentId, loadBoard]);

  // Pending/applied weight proposals are global to weight_model, not scoped to one agent —
  // load independently of the agent picker above.
  const loadProposals = useCallback(() => {
    api
      .getPendingWeightProposals()
      .then((res) => setProposals(res.proposals))
      .catch(() => setProposalError("Failed to load pending weight proposals."));
    api
      .getAppliedWeightProposals()
      .then((res) => setAppliedProposals(res.proposals))
      .catch(() => setProposalError("Failed to load applied weight proposals."));
  }, []);

  useEffect(() => {
    loadProposals();
    const timer = window.setInterval(loadProposals, BOARD_POLL_MS);
    return () => window.clearInterval(timer);
  }, [loadProposals]);

  const applyProposal = useCallback(
    (proposalId: string) => {
      setApplyingId(proposalId);
      setProposalError(null);
      api
        .applyWeightProposal(proposalId)
        .then(() => {
          loadProposals();
          if (agentId) loadBoard(agentId);
        })
        .catch(() => setProposalError(`Failed to apply proposal ${proposalId}.`))
        .finally(() => setApplyingId(null));
    },
    [agentId, loadBoard, loadProposals],
  );

  const rejectProposal = useCallback(
    (proposalId: string) => {
      const reason = window.prompt("Why is this proposal being rejected?");
      if (reason === null || reason.trim() === "") return;
      setRejectingId(proposalId);
      setProposalError(null);
      api
        .rejectWeightProposal(proposalId, reason.trim())
        .then(() => loadProposals())
        .catch(() => setProposalError(`Failed to reject proposal ${proposalId}.`))
        .finally(() => setRejectingId(null));
    },
    [loadProposals],
  );

  const revertProposal = useCallback(
    (proposalId: string) => {
      const reason = window.prompt("Why is this applied change being reverted?");
      if (reason === null || reason.trim() === "") return;
      setRevertingId(proposalId);
      setProposalError(null);
      api
        .revertWeightProposal(proposalId, reason.trim())
        .then(() => {
          loadProposals();
          if (agentId) loadBoard(agentId);
        })
        .catch(() => setProposalError(`Failed to revert proposal ${proposalId}.`))
        .finally(() => setRevertingId(null));
    },
    [agentId, loadBoard, loadProposals],
  );

  const wealthSeries: PnlSeries[] = [
    {
      key: "actual",
      label: "Agent actual",
      color: "#22c55e",
      points: wealthPoints.map((p) => ({ at: p.exit_at, value: p.cumulative_agent_actual_pnl_inr })),
    },
    {
      key: "shadow",
      label: "Shadow (recommendation)",
      color: "#94a3b8",
      points: wealthPoints.map((p) => ({ at: p.exit_at, value: p.cumulative_shadow_pnl_inr })),
    },
  ];

  const hindsightSeries: PnlSeries[] = hindsightCurves.map((c, i) => ({
    key: `rank_${c.candidate_rank}`,
    label: c.candidate_rank === 0 ? "Chosen (rank 0)" : `Alternative (rank ${c.candidate_rank})`,
    color: CANDIDATE_COLORS[i % CANDIDATE_COLORS.length],
    points: c.points.map((p) => ({ at: p.exit_at, value: p.cumulative_shadow_pnl_inr })),
  }));

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Agent Board</h1>
          <p className="text-sm text-muted-foreground">
            Recommendation vs. actual, shadow-track P&amp;L, hindsight comparison, and model-version
            performance for one autonomous agent.
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
            onClick={() => agentId && loadBoard(agentId)}
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

      <section className="space-y-2">
        <h2 className="text-sm font-medium text-muted-foreground">
          Pending weight proposals
          {proposals.length > 0 ? ` (${proposals.length})` : ""}
        </h2>
        {proposalError ? (
          <div className="rounded-xl border border-red-500/40 bg-red-500/5 p-3 text-sm text-red-600 dark:text-red-400">
            {proposalError}
          </div>
        ) : null}
        {proposals.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border/60 bg-muted/10 p-4 text-center text-[11px] text-muted-foreground">
            No pending weight proposals — every self-learning recalibration is either
            already applied or below its clamped-change threshold.
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border bg-card">
            <table className="w-full text-left text-sm">
              <thead className="border-b bg-muted/40 text-[11px] uppercase text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">Proposed</th>
                  <th className="px-3 py-2">Weight</th>
                  <th className="px-3 py-2">Change</th>
                  <th className="px-3 py-2">Rationale</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {proposals.map((p) => (
                  <tr key={p.id} className="border-b last:border-0 align-top">
                    <td className="px-3 py-2 text-xs text-muted-foreground">
                      {new Date(p.created_at).toLocaleString("en-IN")}
                    </td>
                    <td className="px-3 py-2 font-medium">{p.weight_id}</td>
                    <td className="px-3 py-2">
                      {p.current_value.toFixed(3)} → {p.proposed_value.toFixed(3)}
                    </td>
                    <td className="px-3 py-2 max-w-md text-xs text-muted-foreground">{p.rationale}</td>
                    <td className="px-3 py-2">
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => applyProposal(p.id)}
                          disabled={applyingId === p.id || rejectingId === p.id}
                          className="rounded-md border bg-background px-3 py-1.5 text-xs font-medium transition-colors hover:bg-muted disabled:opacity-50"
                        >
                          {applyingId === p.id ? "Applying…" : "Apply"}
                        </button>
                        <button
                          type="button"
                          onClick={() => rejectProposal(p.id)}
                          disabled={applyingId === p.id || rejectingId === p.id}
                          className="rounded-md border border-red-500/40 bg-background px-3 py-1.5 text-xs font-medium text-red-600 transition-colors hover:bg-red-500/10 disabled:opacity-50 dark:text-red-400"
                        >
                          {rejectingId === p.id ? "Rejecting…" : "Reject"}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-medium text-muted-foreground">
          Recently applied weight changes
          {appliedProposals.length > 0 ? ` (${appliedProposals.length})` : ""}
        </h2>
        {appliedProposals.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border/60 bg-muted/10 p-4 text-center text-[11px] text-muted-foreground">
            No applied weight changes yet.
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border bg-card">
            <table className="w-full text-left text-sm">
              <thead className="border-b bg-muted/40 text-[11px] uppercase text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">Applied</th>
                  <th className="px-3 py-2">Weight</th>
                  <th className="px-3 py-2">Change</th>
                  <th className="px-3 py-2">Rationale</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {[...appliedProposals]
                  .reverse()
                  .slice(0, 10)
                  .map((p) => (
                    <tr key={p.id} className="border-b last:border-0 align-top">
                      <td className="px-3 py-2 text-xs text-muted-foreground">
                        {p.applied_at ? new Date(p.applied_at).toLocaleString("en-IN") : "—"}
                      </td>
                      <td className="px-3 py-2 font-medium">{p.weight_id}</td>
                      <td className="px-3 py-2">
                        {p.current_value.toFixed(3)} → {p.proposed_value.toFixed(3)}
                      </td>
                      <td className="px-3 py-2 max-w-md text-xs text-muted-foreground">{p.rationale}</td>
                      <td className="px-3 py-2">
                        <button
                          type="button"
                          onClick={() => revertProposal(p.id)}
                          disabled={revertingId === p.id}
                          className="rounded-md border border-amber-500/40 bg-background px-3 py-1.5 text-xs font-medium text-amber-700 transition-colors hover:bg-amber-500/10 disabled:opacity-50 dark:text-amber-400"
                        >
                          {revertingId === p.id ? "Reverting…" : "Revert"}
                        </button>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {!agentId ? (
        <div className="rounded-xl border border-dashed border-border/60 bg-muted/10 p-6 text-center text-sm text-muted-foreground">
          No autonomous agents exist yet.
        </div>
      ) : loading && !summary ? (
        <div className="rounded-xl border border-dashed border-border/60 bg-muted/10 p-6 text-center text-sm text-muted-foreground">
          Loading…
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatCard
              label="Agent actual P&L"
              value={fmtInr(summary?.pnl_summary.agent_actual_total_pnl_inr)}
              tone={toneFor(summary?.pnl_summary.agent_actual_total_pnl_inr)}
            />
            <StatCard
              label="Shadow P&L"
              value={fmtInr(summary?.pnl_summary.shadow_total_pnl_inr)}
              tone={toneFor(summary?.pnl_summary.shadow_total_pnl_inr)}
            />
            <StatCard
              label="Shadow − actual"
              value={fmtInr(summary?.pnl_summary.shadow_minus_actual_inr)}
              tone={toneFor(summary?.pnl_summary.shadow_minus_actual_inr)}
            />
            <StatCard
              label="Choice alignment rate"
              value={fmtPct(summary?.alignment.alignment_rate)}
            />
          </div>

          <section className="space-y-2">
            <h2 className="text-sm font-medium text-muted-foreground">Wealth curve</h2>
            <AgentPnlCurveChart series={wealthSeries} emptyLabel="No closed trades yet for this agent." />
          </section>

          <section className="space-y-2">
            <h2 className="text-sm font-medium text-muted-foreground">
              Hindsight comparison — every ranked candidate
              {hindsight?.best_candidate_rank != null
                ? ` (best in hindsight: rank ${hindsight.best_candidate_rank})`
                : ""}
            </h2>
            <AgentPnlCurveChart
              series={hindsightSeries}
              emptyLabel="No multi-candidate hindsight data yet for this agent."
            />
          </section>

          {hindsight && hindsight.attribution_factor_rollup.length > 0 ? (
            <section className="space-y-2">
              <h2 className="text-sm font-medium text-muted-foreground">
                Factor attribution — why the winning alternative did better
              </h2>
              <div className="overflow-x-auto rounded-xl border bg-card">
                <table className="w-full text-left text-sm">
                  <thead className="border-b bg-muted/40 text-[11px] uppercase text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2">Factor</th>
                      <th className="px-3 py-2">Favored winner</th>
                      <th className="px-3 py-2">Favored choice</th>
                      <th className="px-3 py-2">Avg magnitude</th>
                    </tr>
                  </thead>
                  <tbody>
                    {hindsight.attribution_factor_rollup.map((row) => (
                      <tr key={row.factor} className="border-b last:border-0">
                        <td className="px-3 py-2 font-medium">{row.factor}</td>
                        <td className="px-3 py-2">{row.favored_winner_count}</td>
                        <td className="px-3 py-2">{row.favored_choice_count}</td>
                        <td className="px-3 py-2">{row.average_magnitude.toFixed(4)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}

          <section className="space-y-2">
            <h2 className="text-sm font-medium text-muted-foreground">Model-version timeline</h2>
            {timeline.length === 0 ? (
              <div className="rounded-xl border border-dashed border-border/60 bg-muted/10 p-4 text-center text-[11px] text-muted-foreground">
                No weight changes have been applied yet.
              </div>
            ) : (
              <div className="overflow-x-auto rounded-xl border bg-card">
                <table className="w-full text-left text-sm">
                  <thead className="border-b bg-muted/40 text-[11px] uppercase text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2">Applied</th>
                      <th className="px-3 py-2">Weight</th>
                      <th className="px-3 py-2">Change</th>
                      <th className="px-3 py-2">Win rate before → after</th>
                      <th className="px-3 py-2">Net P&L before → after</th>
                    </tr>
                  </thead>
                  <tbody>
                    {timeline.map((entry) => (
                      <tr key={entry.proposal_id} className="border-b last:border-0 align-top">
                        <td className="px-3 py-2 text-xs text-muted-foreground">
                          {new Date(entry.applied_at).toLocaleString("en-IN")}
                        </td>
                        <td className="px-3 py-2 font-medium">{entry.weight_id}</td>
                        <td className="px-3 py-2">
                          {entry.value_before.toFixed(3)} → {entry.value_after.toFixed(3)}
                        </td>
                        <td className="px-3 py-2">
                          {fmtPct(entry.performance_before.win_rate)} → {fmtPct(entry.performance_after.win_rate)}
                        </td>
                        <td className="px-3 py-2">
                          {fmtInr(entry.performance_before.net_pnl)} → {fmtInr(entry.performance_after.net_pnl)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
