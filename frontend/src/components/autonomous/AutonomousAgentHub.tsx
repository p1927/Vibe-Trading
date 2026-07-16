import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Radio } from "lucide-react";
import { toast } from "sonner";
import { api, type AutonomousAgentInstance, type AutonomousStackHealth } from "@/lib/api";
import { AutonomousAgentCard } from "@/components/autonomous/AutonomousAgentCard";
import { cn } from "@/lib/utils";

const POLL_MS = 15_000;

function StackHealthStrip({ health }: { health: AutonomousStackHealth | undefined }) {
  if (!health) return null;
  const sched = health.scheduler_health ?? "unknown";
  const nautilusOn = health.nautilus_watch_enabled !== false;
  const nautilusAlive = health.nautilus_process_alive;

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
      <span className="font-medium text-foreground/80">Trader vs infra</span>
      <span
        className={cn(
          "rounded border px-1.5 py-0.5",
          sched === "ok" && "border-emerald-500/40 text-emerald-700",
          sched === "stale" && "border-amber-500/40 text-amber-700",
        )}
      >
        scheduler {sched}
      </span>
      <span
        className={cn(
          "rounded border px-1.5 py-0.5",
          !nautilusOn && "text-muted-foreground",
          nautilusOn && nautilusAlive && "border-emerald-500/40 text-emerald-700",
          nautilusOn && !nautilusAlive && "border-amber-500/40 text-amber-700",
        )}
      >
        nautilus {nautilusOn ? (nautilusAlive ? "running" : "expected") : "off"}
      </span>
      {health.market_open != null && (
        <span>market {health.market_open ? "open" : "closed"}</span>
      )}
      {health.paper_session_enabled && <span>paper session active</span>}
    </div>
  );
}

interface Props {
  onCreateAgent: () => void;
}

export function AutonomousAgentHub({ onCreateAgent }: Props) {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<AutonomousAgentInstance[]>([]);
  const [stackHealth, setStackHealth] = useState<AutonomousStackHealth | undefined>();
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const res = await api.listAutonomousAgents();
      setAgents(Array.isArray(res.agents) ? res.agents : []);
      setStackHealth(res.stack_health);
    } catch (err) {
      console.warn("Failed to load autonomous agents", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, POLL_MS);
    const onRefresh = () => void load();
    window.addEventListener("autonomous-agents-refresh", onRefresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("autonomous-agents-refresh", onRefresh);
    };
  }, [load]);

  const openAgent = (agent: AutonomousAgentInstance) => {
    const session = agent.vibe_session_id;
    if (!session) {
      toast.error("Agent has no bound session");
      return;
    }
    navigate(`/autonomous?agent=${encodeURIComponent(agent.id)}&session=${encodeURIComponent(session)}`);
  };

  const handlePause = async (id: string) => {
    try {
      await api.pauseAutonomousAgent(id);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Pause failed");
    }
  };

  const handleResume = async (id: string) => {
    try {
      await api.resumeAutonomousAgent(id);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Resume failed");
    }
  };

  const handleDelete = async (id: string) => {
    if (!window.confirm("Delete this autonomous agent?")) return;
    try {
      await api.deleteAutonomousAgent(id);
      await load();
      toast.success("Agent deleted");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Delete failed");
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Autonomous</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Persistent trading agents — Nautilus watches, Vibe decides, OpenAlgo executes.
        </p>
      </div>

      <StackHealthStrip health={stackHealth} />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <button
          type="button"
          onClick={onCreateAgent}
          className="flex min-h-[140px] flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-primary/40 bg-primary/5 p-4 text-center transition-colors hover:border-primary hover:bg-primary/10"
        >
          <Plus className="h-8 w-8 text-primary" />
          <span className="text-sm font-medium text-foreground">Create agent</span>
          <span className="text-xs text-muted-foreground">Describe what you want in chat</span>
        </button>

        {loading && (
          <div className="col-span-full text-sm text-muted-foreground">Loading agents…</div>
        )}

        {!loading && agents.length === 0 && (
          <div className="col-span-full flex items-center gap-2 rounded-xl border bg-muted/20 p-4 text-sm text-muted-foreground">
            <Radio className="h-4 w-4" />
            No agents yet — click Create agent to get started.
          </div>
        )}

        {agents.map((agent) => (
          <AutonomousAgentCard
            key={agent.id}
            agent={agent}
            onOpen={() => openAgent(agent)}
            onPause={agent.status === "running" ? () => handlePause(agent.id) : undefined}
            onResume={agent.status === "paused" ? () => handleResume(agent.id) : undefined}
            onDelete={() => handleDelete(agent.id)}
          />
        ))}
      </div>
    </div>
  );
}
