import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import { api, type AutonomousAgentInstance } from "@/lib/api";
import { Agent } from "@/pages/Agent";
import { AutonomousAgentHub } from "@/components/autonomous/AutonomousAgentHub";
import { cn } from "@/lib/utils";

const RUNTIME_POLL_MS = 15_000;

function AgentRuntimeStrip({ agent }: { agent: AutonomousAgentInstance }) {
  const runtime = agent.runtime;
  if (!runtime) return null;

  const sched = runtime.scheduler_health ?? "unknown";
  const nautilusOn = runtime.nautilus_watch_enabled !== false;
  const nautilusAlive = runtime.nautilus_process_alive;
  const lastDecision = runtime.last_decision as { decision?: string } | null;

  return (
    <div className="ml-auto flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
      {lastDecision?.decision && (
        <span className="font-medium text-foreground/80">last {lastDecision.decision}</span>
      )}
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
        nautilus {nautilusOn ? (nautilusAlive ? "on" : "expected") : "off"}
      </span>
      {runtime.handoff_active && (
        <span className="rounded border border-primary/30 px-1.5 py-0.5 text-primary">position</span>
      )}
    </div>
  );
}

export function Autonomous() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const sessionId = searchParams.get("session");
  const agentId = searchParams.get("agent");
  const isOrchestrator = agentId === "orchestrator" || searchParams.get("create") === "1";
  const [agent, setAgent] = useState<AutonomousAgentInstance | null>(null);

  const loadAgent = useCallback(async () => {
    if (!agentId || agentId === "orchestrator") {
      setAgent(null);
      return;
    }
    try {
      const a = await api.getAutonomousAgent(agentId);
      setAgent(a);
    } catch {
      setAgent(null);
    }
  }, [agentId]);

  useEffect(() => {
    void loadAgent();
    if (!agentId || agentId === "orchestrator") return;
    const timer = window.setInterval(() => void loadAgent(), RUNTIME_POLL_MS);
    return () => window.clearInterval(timer);
  }, [agentId, loadAgent]);

  const openOrchestrator = useCallback(async () => {
    try {
      const orch = await api.getOrchestratorSession();
      setSearchParams({
        agent: "orchestrator",
        session: orch.session_id,
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to open orchestrator");
    }
  }, [setSearchParams]);

  const goBack = useCallback(() => {
    navigate("/autonomous");
  }, [navigate]);

  // After promotion, vibe_session_id === orchestrator session id; only agent param changes.
  const onAgentCommitted = useCallback(
    (newAgentId: string, newSessionId: string) => {
      setSearchParams({
        agent: newAgentId,
        session: newSessionId,
      });
    },
    [setSearchParams],
  );

  if (sessionId && (agentId || isOrchestrator)) {
    const title =
      agentId === "orchestrator"
        ? "Create agent"
        : agent?.name || "Autonomous agent";

    return (
      <div className="flex h-[calc(100vh-0px)] flex-col">
        <header className="flex shrink-0 items-center gap-3 border-b border-border/60 bg-background/95 px-4 py-2">
          <button
            type="button"
            onClick={goBack}
            className="inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-xs font-medium text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Back to agents
          </button>
          <span className="text-sm font-medium text-foreground">{title}</span>
          {agent && agentId !== "orchestrator" && <AgentRuntimeStrip agent={agent} />}
        </header>
        <div className="min-h-0 flex-1">
          <Agent
            embedded
            backToAutonomous={goBack}
            onAutonomousAgentCommitted={onAgentCommitted}
          />
        </div>
      </div>
    );
  }

  return <AutonomousAgentHub onCreateAgent={() => void openOrchestrator()} />;
}
