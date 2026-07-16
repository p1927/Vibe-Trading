import { memo, useCallback, useState } from "react";
import { Check, Loader2, Radio, ShieldCheck, X } from "lucide-react";
import { toast } from "sonner";
import { api, type AutonomousAgentProposal } from "@/lib/api";
import { AgentAvatar } from "@/components/chat/AgentAvatar";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";

interface Props {
  proposal: AutonomousAgentProposal;
  committed?: { agent_id?: string; name?: string } | null;
  onAdjust: (message: string) => void;
  onCommitted?: (agentId: string, sessionId: string) => void;
}

function formatMs(ms: number | undefined): string {
  if (!ms || !Number.isFinite(ms)) return "—";
  const min = Math.round(ms / 60_000);
  if (min < 60) return `${min} min`;
  const hr = Math.round((min / 60) * 10) / 10;
  return `${hr} hr`;
}

export const AutonomousAgentProposalCard = memo(function AutonomousAgentProposalCard({
  proposal,
  committed,
  onAdjust,
  onCommitted,
}: Props) {
  const [busy, setBusy] = useState(false);
  const [adjusting, setAdjusting] = useState(false);
  const [adjustText, setAdjustText] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);

  const handleCommit = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    try {
      const result = await api.commitAutonomousAgent({
        proposal_id: proposal.proposal_id,
        consent_ack: true,
        session_id: proposal.session_id,
      });
      toast.success(`Agent "${result.agent.name}" is now running`);
      onCommitted?.(result.agent.id, result.vibe_session_id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to create agent");
      setBusy(false);
    }
  }, [busy, onCommitted, proposal.proposal_id, proposal.session_id]);

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
      <div className="flex-1 min-w-0 space-y-3 rounded-2xl border border-primary/20 bg-background/95 p-4 shadow-sm">
        <div className="flex items-start gap-2">
          <Radio className="h-4 w-4 shrink-0 text-primary" />
          <div>
            <p className="text-sm font-semibold text-foreground">Create autonomous agent</p>
            <p className="text-xs text-muted-foreground">{proposal.name}</p>
          </div>
        </div>

        <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[11px]">
          <div className="col-span-2">
            <dt className="text-muted-foreground">Symbols</dt>
            <dd className="font-medium">{proposal.symbols.join(", ")}</dd>
          </div>
          <div className="col-span-2">
            <dt className="text-muted-foreground">Mandate</dt>
            <dd className="font-medium text-foreground">{proposal.mandate || "—"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Budget</dt>
            <dd className="font-mono">₹{(constraints.budget_inr ?? 0).toLocaleString()}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Max loss</dt>
            <dd className="font-mono">₹{(constraints.max_daily_loss_inr ?? 0).toLocaleString()}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Confidence</dt>
            <dd>{constraints.confidence_threshold ?? 75}% to act</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Mode</dt>
            <dd>{constraints.mode || "paper"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Watch</dt>
            <dd>every {formatMs(schedules.watch_ms)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Research</dt>
            <dd>every {formatMs(schedules.research_ms)}</dd>
          </div>
        </dl>

        {adjusting ? (
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
              disabled={busy}
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
