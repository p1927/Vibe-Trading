import { memo, useCallback, useMemo, useState } from "react";
import { Check, Loader2, Radio, ShieldCheck, X } from "lucide-react";
import { toast } from "sonner";
import { api, type AutonomousAgentProposal, type AutonomousStackHealth } from "@/lib/api";
import { AgentAvatar } from "@/components/chat/AgentAvatar";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { cn } from "@/lib/utils";

interface Props {
  proposal: AutonomousAgentProposal;
  committed?: { agent_id?: string; name?: string } | null;
  onAdjust: (message: string) => void;
  onCommitted?: (agentId: string, sessionId: string) => void;
  onDismiss?: () => void;
}

function formatMs(ms: number | undefined): string {
  if (!ms || !Number.isFinite(ms)) return "—";
  const min = Math.round(ms / 60_000);
  if (min < 60) return `${min} min`;
  const hr = Math.round((min / 60) * 10) / 10;
  return `${hr} hr`;
}

function isProposalExpired(proposal: AutonomousAgentProposal): boolean {
  const expires = proposal.expires_at_ms;
  return Boolean(expires && Date.now() > expires);
}

function formatInstruments(proposal: AutonomousAgentProposal, market?: "IN" | "US"): string {
  const raw = proposal.mandate_config?.allowed_instruments as string[] | undefined;
  if (raw?.length) {
    return raw.map((x) => x.charAt(0).toUpperCase() + x.slice(1)).join(" · ");
  }
  return market === "US" ? "Equity" : "Equity";
}

function formatMoney(amount: number | undefined, market: "IN" | "US" | undefined): string {
  const value = amount ?? 0;
  if (market === "US") {
    return `$${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  }
  return `₹${value.toLocaleString()}`;
}

function ProposalInfraStrip({
  market,
  backend,
  health,
}: {
  market?: "IN" | "US";
  backend?: string;
  health?: AutonomousStackHealth;
}) {
  if (!health && !market) return null;

  const nautilusOn = health?.nautilus_watch_enabled !== false;
  const nautilusAlive = health?.nautilus_process_alive;
  const sched = health?.scheduler_health ?? "unknown";

  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
      <span className="font-medium text-muted-foreground">Infra</span>
      {market === "US" ? (
        <span
          className={cn(
            "rounded border px-1.5 py-0.5",
            backend === "alpaca" ? "border-emerald-500/40 text-emerald-700" : "border-muted text-muted-foreground",
          )}
        >
          Alpaca paper
        </span>
      ) : (
        <>
          <span
            className={cn(
              "rounded border px-1.5 py-0.5",
              health?.paper_session_enabled
                ? "border-emerald-500/40 text-emerald-700"
                : "border-amber-500/40 text-amber-700",
            )}
          >
            OpenAlgo {health?.paper_session_enabled ? "ready" : "check"}
          </span>
          <span
            className={cn(
              "rounded border px-1.5 py-0.5",
              !nautilusOn && "text-muted-foreground",
              nautilusOn && nautilusAlive && "border-emerald-500/40 text-emerald-700",
              nautilusOn && !nautilusAlive && "border-amber-500/40 text-amber-700",
            )}
          >
            Nautilus {nautilusOn ? (nautilusAlive ? "running" : "start watch") : "off"}
          </span>
        </>
      )}
      {market !== "US" && (
        <span
          className={cn(
            "rounded border px-1.5 py-0.5",
            sched === "ok" && "border-emerald-500/40 text-emerald-700",
            sched === "stale" && "border-amber-500/40 text-amber-700",
          )}
        >
          scheduler {sched}
        </span>
      )}
    </div>
  );
}

export const AutonomousAgentProposalCard = memo(function AutonomousAgentProposalCard({
  proposal,
  committed,
  onAdjust,
  onCommitted,
  onDismiss,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [adjusting, setAdjusting] = useState(false);
  const [adjustText, setAdjustText] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);

  const expired = useMemo(() => isProposalExpired(proposal), [proposal]);
  const superseded = Boolean(proposal.superseded);
  const market = proposal.execution_market;
  const routingErrors = proposal.routing_errors ?? [];
  const hasRoutingErrors = routingErrors.length > 0;
  const blocked = expired || superseded || hasRoutingErrors;

  const handleCommit = useCallback(async () => {
    if (busy || blocked) return;
    setBusy(true);
    try {
      const result = await api.commitAutonomousAgent({
        proposal_id: proposal.proposal_id,
        consent_ack: true,
        session_id: proposal.session_id ?? proposal.orchestrator_session_id,
      });
      toast.success(`Agent "${result.agent.name}" is now running`);
      if (result.paper_session_warnings?.length) {
        for (const warning of result.paper_session_warnings) {
          toast.warning(warning);
        }
      }
      window.dispatchEvent(new Event("autonomous-agents-refresh"));
      onCommitted?.(result.agent.id, result.vibe_session_id);
      setBusy(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to create agent");
      setBusy(false);
    }
  }, [blocked, busy, onCommitted, proposal.orchestrator_session_id, proposal.proposal_id, proposal.session_id]);

  if (committed) {
    return (
      <div className="flex gap-3">
        <AgentAvatar />
        <span className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-500/10 px-2.5 py-1 text-xs font-medium text-emerald-600 dark:text-emerald-400">
          <ShieldCheck className="h-3 w-3" />
          Autonomous agent active{committed.name ? ` · ${committed.name}` : ""}
        </span>
      </div>
    );
  }

  const constraints = proposal.constraints || {};
  const schedules = proposal.schedules || {};

  return (
    <div className="flex gap-3">
      <AgentAvatar />
      <div
        className={cn(
          "flex-1 min-w-0 space-y-3 rounded-2xl border border-primary/20 bg-background/95 p-4 shadow-sm",
          superseded && "opacity-60",
        )}
      >
        <div className="flex items-start gap-2">
          <Radio className="h-4 w-4 shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-foreground">Create autonomous agent</p>
            <p className="text-xs text-muted-foreground">{proposal.name}</p>
            {expired ? (
              <p className="mt-1 text-xs font-medium text-amber-700 dark:text-amber-300">
                Proposal expired — re-propose or dismiss.
              </p>
            ) : null}
            {superseded ? (
              <p className="mt-1 text-xs font-medium text-muted-foreground">
                Superseded — use the latest proposal card above.
              </p>
            ) : null}
          </div>
        </div>

        <ProposalInfraStrip
          market={market}
          backend={proposal.execution_backend}
          health={proposal.stack_health}
        />

        {hasRoutingErrors ? (
          <div className="rounded-lg border border-red-500/40 bg-red-500/5 px-3 py-2 text-[11px] text-red-700 dark:text-red-300">
            <p className="font-medium">Routing error — cannot start agent until fixed:</p>
            <ul className="mt-1 list-disc pl-4">
              {routingErrors.map((err) => (
                <li key={err}>{err}</li>
              ))}
            </ul>
          </div>
        ) : null}

        <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
          <div className="col-span-2">
            <dt className="text-muted-foreground">Symbols</dt>
            <dd className="font-medium">{(proposal.symbols ?? []).join(", ") || "—"}</dd>
          </div>
          <div className="col-span-2">
            <dt className="text-muted-foreground">Mandate</dt>
            <dd className="font-medium text-foreground">{proposal.mandate || "—"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Budget</dt>
            <dd className="font-mono">{formatMoney(constraints.budget_inr, market)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Max loss</dt>
            <dd className="font-mono">{formatMoney(constraints.max_daily_loss_inr, market)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Confidence</dt>
            <dd>{constraints.confidence_threshold ?? 75}% to act</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Mode</dt>
            <dd>{constraints.mode || "paper"}</dd>
          </div>
          <div className="col-span-2">
            <dt className="text-muted-foreground">Instruments</dt>
            <dd className="font-medium">{formatInstruments(proposal, market)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Watch</dt>
            <dd>every {formatMs(schedules.watch_ms)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Research</dt>
            <dd>every {formatMs(schedules.research_ms)}</dd>
          </div>
          {market ? (
            <div className="col-span-2">
              <dt className="text-muted-foreground">Execution</dt>
              <dd>{market === "US" ? "US · Alpaca paper" : "India · Nautilus watch + OpenAlgo"}</dd>
            </div>
          ) : null}
        </dl>

        {expired || superseded ? (
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() =>
                onAdjust(
                  superseded
                    ? "Please re-propose with the latest settings."
                    : "Please refresh this proposal with the same settings.",
                )
              }
              className="rounded-lg border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted/50"
            >
              Re-propose
            </button>
            {onDismiss ? (
              <button
                type="button"
                onClick={onDismiss}
                className="rounded-lg border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
              >
                Dismiss
              </button>
            ) : null}
          </div>
        ) : adjusting ? (
          <div className="grid gap-2">
            <input
              type="text"
              value={adjustText}
              autoFocus
              onChange={(e) => setAdjustText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && adjustText.trim()) {
                  onAdjust(adjustText.trim());
                  setAdjusting(false);
                  setAdjustText("");
                } else if (e.key === "Escape") setAdjusting(false);
              }}
              placeholder="Describe what to change…"
              className="w-full rounded-lg border bg-background px-3 py-1.5 text-xs outline-none focus:ring-2 focus:ring-primary/30"
            />
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setAdjusting(false)} className="text-xs text-muted-foreground">
                <X className="inline h-3 w-3" /> Cancel
              </button>
              <button
                type="button"
                disabled={!adjustText.trim()}
                onClick={() => {
                  onAdjust(adjustText.trim());
                  setAdjusting(false);
                  setAdjustText("");
                }}
                className="text-xs text-primary"
              >
                Send adjustment
              </button>
            </div>
          </div>
        ) : (
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setAdjusting(true)}
              disabled={busy}
              className="rounded-lg border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-foreground disabled:opacity-40"
            >
              Adjust
            </button>
            <button
              type="button"
              onClick={() => setConfirmOpen(true)}
              disabled={busy || hasRoutingErrors}
              className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-40"
            >
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
              {busy ? "Creating…" : "Confirm & start agent"}
            </button>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirmOpen}
        title="Start autonomous agent?"
        description="This agent will run on a schedule, watch markets, and paper-trade when confident."
        confirmLabel="Start agent"
        cancelLabel="Cancel"
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => {
          setConfirmOpen(false);
          void handleCommit();
        }}
      />
    </div>
  );
});
