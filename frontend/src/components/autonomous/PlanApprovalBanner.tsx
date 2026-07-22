import { Check, Loader2, Shield } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { WatchersPanel } from "@/components/research/WatchersPanel";
import { api, type AutonomousAgentInstance } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  agent: AutonomousAgentInstance;
  onApproved?: () => void;
}

export function WatchSpecPanel({ agent }: { agent: AutonomousAgentInstance }) {
  return <WatchersPanel agentId={agent.id} />;
}

export function PlanApprovalBanner({ agent, onApproved }: Props) {
  const [loading, setLoading] = useState(false);
  const awaiting =
    agent.bootstrap_status === "awaiting_plan_approval" || agent.plan_approval_required;
  const approved = Boolean(agent.plan_approved_at) || agent.bootstrap_status === "done";

  if (!awaiting || approved) return null;

  const strategy = agent.thesis?.strategy || "—";
  const confidence = agent.thesis?.confidence;

  const handleApprove = async () => {
    setLoading(true);
    try {
      await api.approveAutonomousPlan(agent.id);
      toast.success("Plan approved — Nautilus watchers are live");
      onApproved?.();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Approval failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mx-4 mt-3 rounded-xl border border-primary/40 bg-primary/5 p-4 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex items-center gap-2 font-semibold text-foreground">
            <Shield className="h-4 w-4 text-primary" />
            Approve initial trade plan
          </div>
          <p className="text-xs text-muted-foreground">
            Strategy <span className="font-mono text-foreground">{strategy}</span>
            {confidence != null ? ` · confidence ${confidence}%` : ""}
            — approve once to start autonomous watching. Revisions come from Nautilus alerts only.
          </p>
        </div>
        <button
          type="button"
          disabled={loading}
          onClick={() => void handleApprove()}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground",
            loading && "opacity-70",
          )}
        >
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
          Approve &amp; start watching
        </button>
      </div>
      <div className="mt-3">
        <WatchSpecPanel agent={agent} />
      </div>
    </div>
  );
}
