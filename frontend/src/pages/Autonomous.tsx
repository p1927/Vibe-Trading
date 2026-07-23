import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import { api, type AutonomousAgentInstance } from "@/lib/api";
import { AutonomousAgentHub } from "@/components/autonomous/AutonomousAgentHub";
import { PlanApprovalBanner } from "@/components/autonomous/PlanApprovalBanner";
import { cn } from "@/lib/utils";

const EmbeddedAgent = lazy(() =>
  import("@/pages/Agent").then((m) => ({ default: m.Agent })),
);

const RUNTIME_POLL_MS = 15_000;
const RUNTIME_POLL_BOOTSTRAP_MS = 3_000;

export type AutonomousAgentLoadState = "idle" | "loading" | "ready" | "error";

function AgentRuntimeStrip({ agent }: { agent: AutonomousAgentInstance }) {
  const runtime = agent.runtime;
  if (!runtime) return null;

  const sched = runtime.scheduler_health ?? "unknown";
  const nautilusState = runtime.nautilus_state;
  const nautilusOn = runtime.nautilus_watch_enabled !== false;
  const lastDecision = runtime.last_decision as { decision?: string; confidence?: number } | null;
  const confidence = agent.thesis?.confidence ?? lastDecision?.confidence;
  const isBootstrapping =
    agent.streaming ||
    sched === "initializing" ||
    agent.bootstrap_status === "pending" ||
    agent.bootstrap_status === "running" ||
    runtime?.bootstrap_status === "pending" ||
    runtime?.bootstrap_status === "running";
  const bootstrapFailed =
    sched === "bootstrap_failed" ||
    agent.bootstrap_status === "failed" ||
    runtime?.bootstrap_status === "failed";
  const bootstrapError = agent.bootstrap_error || runtime?.bootstrap_error;
  const infraPaused = agent.status === "paused" && agent.pause_reason === "infra";
  const infraPending = agent.infra_pending?.[0];

  const nautilusLabel =
    !nautilusOn
      ? "off"
      : nautilusState === "node_on"
        ? "on"
        : nautilusState === "poll_ok"
          ? "poll"
          : nautilusState === "stale"
            ? "stale"
            : "expected";

  return (
    <div className="ml-auto flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
      {bootstrapFailed && <span className="font-medium text-red-600">bootstrap failed</span>}
      {bootstrapFailed && bootstrapError && (
        <span className="max-w-xs truncate text-red-600/80" title={bootstrapError}>
          {bootstrapError}
        </span>
      )}
      {infraPaused && (
        <span
          className="max-w-xs truncate font-medium text-amber-700 dark:text-amber-300"
          title={infraPending || "Waiting for OpenAlgo / Nautilus — run trade status"}
        >
          waiting for infra{infraPending ? ` · ${infraPending}` : ""}
        </span>
      )}
      {isBootstrapping && !bootstrapFailed && !infraPaused && (
        <span className="font-medium text-primary">starting…</span>
      )}
      {lastDecision?.decision && (
        <span className="font-medium text-foreground/80">
          last {lastDecision.decision}
          {confidence != null ? ` · conf ${confidence}%` : ""}
        </span>
      )}
      <span
        className={cn(
          "rounded border px-1.5 py-0.5",
          sched === "ok" && "border-emerald-500/40 text-emerald-700",
          sched === "initializing" && "border-primary/40 text-primary",
          sched === "bootstrap_failed" && "border-red-500/40 text-red-700",
          sched === "stale" && "border-amber-500/40 text-amber-700",
        )}
      >
        scheduler {sched}
      </span>
      <span
        className={cn(
          "rounded border px-1.5 py-0.5",
          !nautilusOn && "text-muted-foreground",
          nautilusState === "node_on" && "border-emerald-500/40 text-emerald-700",
          nautilusState === "poll_ok" && "border-emerald-500/40 text-emerald-700",
          nautilusState === "expected" && "text-muted-foreground",
          nautilusState === "stale" && "border-amber-500/40 text-amber-700",
        )}
      >
        nautilus {nautilusLabel}
      </span>
      {runtime.position_tracked && (
        <span className="rounded border border-primary/30 px-1.5 py-0.5 text-primary">position</span>
      )}
      {runtime.watch_configured && !runtime.position_tracked && (
        <span className="rounded border px-1.5 py-0.5 text-muted-foreground">watch ready</span>
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
  const [agentLoadState, setAgentLoadState] = useState<AutonomousAgentLoadState>("idle");
  const agentLoadGenRef = useRef(0);

  const loadAgent = useCallback(async () => {
    if (!agentId || agentId === "orchestrator") {
      setAgent(null);
      setAgentLoadState("idle");
      return;
    }
    const gen = agentLoadGenRef.current + 1;
    agentLoadGenRef.current = gen;
    setAgentLoadState((prev) => (prev === "ready" ? prev : "loading"));
    try {
      const a = await api.getAutonomousAgent(agentId);
      if (agentLoadGenRef.current !== gen) return;
      setAgent(a);
      setAgentLoadState("ready");
    } catch {
      if (agentLoadGenRef.current !== gen) return;
      setAgent(null);
      setAgentLoadState("error");
    }
  }, [agentId]);

  const isBootstrappingAgent =
    Boolean(agent?.streaming) ||
    agent?.bootstrap_status === "pending" ||
    agent?.bootstrap_status === "running" ||
    agent?.bootstrap_status === "awaiting_plan_approval" ||
    Boolean(agent?.plan_approval_required) ||
    agent?.runtime?.bootstrap_status === "pending" ||
    agent?.runtime?.bootstrap_status === "running" ||
    agent?.runtime?.scheduler_health === "initializing";

  useEffect(() => {
    void loadAgent();
    if (!agentId || agentId === "orchestrator") return;
    const pollMs = isBootstrappingAgent ? RUNTIME_POLL_BOOTSTRAP_MS : RUNTIME_POLL_MS;
    const timer = window.setInterval(() => void loadAgent(), pollMs);
    const onRefresh = () => {
      void loadAgent();
    };
    window.addEventListener("autonomous-agents-refresh", onRefresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("autonomous-agents-refresh", onRefresh);
    };
  }, [agentId, loadAgent, isBootstrappingAgent]);

  useEffect(() => {
    if (!agentId || agentId === "orchestrator") {
      setAgentLoadState("idle");
      return;
    }
    agentLoadGenRef.current += 1;
    setAgentLoadState("loading");
  }, [agentId]);

  const openDraft = useCallback(async () => {
    try {
      const draft = await api.createDraftAutonomousAgent();
      setSearchParams({
        agent: draft.agent_id,
        session: draft.session_id,
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create draft");
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

  const isDraftView =
    agentId === "orchestrator" || agent?.status === "draft";

  if (sessionId && (agentId || isOrchestrator)) {
    const title = isDraftView ? agent?.name || "Create agent" : agent?.name || "Autonomous agent";

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
          {agent && !isDraftView && <AgentRuntimeStrip agent={agent} />}
        </header>
        {agent && !isDraftView && (
          <PlanApprovalBanner agent={agent} />
        )}
        <div className="min-h-0 flex-1">
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                Loading agent chat…
              </div>
            }
          >
            <EmbeddedAgent
              embedded
              backToAutonomous={goBack}
              onAutonomousAgentCommitted={onAgentCommitted}
              autonomousAgent={agent}
              autonomousAgentId={agentId}
              autonomousAgentLoadState={agentLoadState}
              onAutonomousAgentRefresh={() => void loadAgent()}
            />
          </Suspense>
        </div>
      </div>
    );
  }

  return <AutonomousAgentHub onCreateAgent={() => void openDraft()} />;
}
