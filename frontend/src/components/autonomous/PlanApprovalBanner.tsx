import { AlertTriangle } from "lucide-react";
import { WatchersPanel } from "@/components/research/WatchersPanel";
import type { AutonomousAgentInstance } from "@/lib/api";

interface Props {
  agent: AutonomousAgentInstance;
  onApproved?: () => void;
}

export function WatchSpecPanel({ agent }: { agent: AutonomousAgentInstance }) {
  return <WatchersPanel agentId={agent.id} />;
}

/** Thin strip for rejected plans — primary approve/reject lives on TradePlanWidgetCard. */
export function PlanApprovalBanner({ agent }: Props) {
  if (agent.bootstrap_status !== "plan_rejected") return null;

  return (
    <div className="mx-4 mt-3 rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-sm">
      <div className="flex items-center gap-2 font-semibold text-foreground">
        <AlertTriangle className="h-4 w-4 text-destructive" />
        Plan rejected
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        Revise the strategy in chat — a new trade plan widget will appear for approval.
      </p>
      <div className="mt-3">
        <WatchSpecPanel agent={agent} />
      </div>
    </div>
  );
}
