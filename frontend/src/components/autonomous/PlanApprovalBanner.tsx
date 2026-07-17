import { Check, Eye, Loader2, Shield } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api, type AutonomousAgentInstance } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  agent: AutonomousAgentInstance;
  onApproved?: () => void;
}

function ruleLabel(row: Record<string, unknown>): string {
  const label = String(row.label || row.symbol || "?");
  const metric = String(row.metric || "");
  if (metric === "spot_move_pct") {
    return `${label}: move ${String(row.direction || "either")} ≥${row.threshold}%`;
  }
  if (metric === "level_below" || metric === "level_above") {
    return `${label}: ${metric.replace("_", " ")} ${row.threshold}`;
  }
  if (metric === "session_close") return "Flatten at session close";
  return label;
}

export function WatchSpecPanel({ agent }: { agent: AutonomousAgentInstance }) {
  const watchSpec = (agent.watch_spec || agent.mandate_config?.watch_spec) as
    | { rules?: Array<Record<string, unknown>>; strategy?: string }
    | undefined;
  const rules = watchSpec?.rules || [];
  if (rules.length === 0) return null;

  return (
    <div className="rounded-xl border border-border/60 bg-muted/10 p-3 text-xs space-y-2">
      <div className="flex items-center gap-2 font-semibold text-foreground">
        <Eye className="h-3.5 w-3.5 text-primary" />
        Active watchers
        {watchSpec?.strategy ? (
          <span className="font-mono text-[10px] text-muted-foreground">({watchSpec.strategy})</span>
        ) : null}
      </div>
      <ul className="space-y-1 text-muted-foreground">
        {rules.map((row, i) => (
          <li key={`${String(row.symbol)}-${i}`} className="font-mono text-[11px]">
            · {ruleLabel(row)}
          </li>
        ))}
      </ul>
      {agent.last_watch_at && (
        <p className="text-[10px] text-muted-foreground">
          Last watch: {new Date(agent.last_watch_at).toLocaleString()}
        </p>
      )}
    </div>
  );
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
