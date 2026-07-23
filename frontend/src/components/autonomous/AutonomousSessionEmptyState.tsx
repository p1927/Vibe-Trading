import { Loader2, Radio, ServerCrash, TriangleAlert } from "lucide-react";
import type { AutonomousAgentInstance } from "@/lib/api";
import { cn } from "@/lib/utils";

export type AutonomousAgentLoadState = "idle" | "loading" | "ready" | "error";

interface Props {
  agent: AutonomousAgentInstance | null | undefined;
  agentId?: string | null;
  loadState?: AutonomousAgentLoadState;
}

export function AutonomousSessionEmptyState({ agent, agentId, loadState = "idle" }: Props) {
  const runtime = agent?.runtime;
  const sched = runtime?.scheduler_health ?? "unknown";
  const bootstrapFailed =
    sched === "bootstrap_failed" ||
    agent?.bootstrap_status === "failed" ||
    runtime?.bootstrap_status === "failed";
  const bootstrapError = agent?.bootstrap_error || runtime?.bootstrap_error;
  const infraPaused = agent?.status === "paused" && agent?.pause_reason === "infra";
  const infraPending = agent?.infra_pending?.[0];
  const isBootstrapping =
    Boolean(agent?.streaming) ||
    sched === "initializing" ||
    agent?.bootstrap_status === "pending" ||
    agent?.bootstrap_status === "running" ||
    runtime?.bootstrap_status === "pending" ||
    runtime?.bootstrap_status === "running";
  const finalizeBlocked =
    agent?.bootstrap_status === "running" &&
    Boolean(agent?.last_decision) &&
    !agent?.streaming &&
    !bootstrapFailed;

  if (loadState === "loading" || (agentId && !agent && loadState !== "error")) {
    return (
      <div className="mx-auto max-w-lg space-y-3 rounded-xl border border-primary/30 bg-primary/5 px-4 py-5 text-sm">
        <div className="flex items-center gap-2 font-medium text-primary">
          <Loader2 className={cn("h-4 w-4 shrink-0 animate-spin")} />
          Loading agent status
        </div>
        <p className="text-muted-foreground">
          Fetching bootstrap and runtime state. Watch summaries and research turns will appear here shortly.
        </p>
      </div>
    );
  }

  if (loadState === "error" && agentId && !agent) {
    return (
      <div className="mx-auto max-w-lg space-y-3 rounded-xl border border-red-500/40 bg-red-500/5 px-4 py-5 text-sm">
        <div className="flex items-center gap-2 font-medium text-red-700 dark:text-red-300">
          <ServerCrash className="h-4 w-4 shrink-0" />
          Agent not found
        </div>
        <p className="text-red-800/90 dark:text-red-200/90">
          Could not load agent <span className="font-mono">{agentId}</span>. Return to the hub or create a new agent.
        </p>
      </div>
    );
  }

  if (finalizeBlocked) {
    return (
      <div className="mx-auto max-w-lg space-y-3 rounded-xl border border-amber-500/40 bg-amber-500/5 px-4 py-5 text-sm">
        <div className="flex items-center gap-2 font-medium text-amber-800 dark:text-amber-200">
          <Loader2 className={cn("h-4 w-4 shrink-0 animate-spin")} />
          Finalizing bootstrap plan
        </div>
        <p className="text-amber-900/90 dark:text-amber-100/90">
          A decision was recorded but the structured options plan is still being built. Recovery will retry
          automatically, or send guidance in chat.
        </p>
      </div>
    );
  }

  if (bootstrapFailed) {
    return (
      <div className="mx-auto max-w-lg space-y-3 rounded-xl border border-red-500/40 bg-red-500/5 px-4 py-5 text-sm">
        <div className="flex items-center gap-2 font-medium text-red-700 dark:text-red-300">
          <ServerCrash className="h-4 w-4 shrink-0" />
          Bootstrap failed
        </div>
        <p className="text-red-800/90 dark:text-red-200/90">
          The first watch and research turn did not complete. Check stack health with{" "}
          <span className="font-mono">trade status</span>, then resume or create a new agent.
        </p>
        {bootstrapError && (
          <p className="rounded-lg border border-red-500/20 bg-background/60 px-3 py-2 font-mono text-xs text-red-700 dark:text-red-300">
            {bootstrapError}
          </p>
        )}
      </div>
    );
  }

  if (infraPaused) {
    return (
      <div className="mx-auto max-w-lg space-y-3 rounded-xl border border-amber-500/40 bg-amber-500/5 px-4 py-5 text-sm">
        <div className="flex items-center gap-2 font-medium text-amber-800 dark:text-amber-200">
          <TriangleAlert className="h-4 w-4 shrink-0" />
          Waiting for infrastructure
        </div>
        <p className="text-amber-900/90 dark:text-amber-100/90">
          Bootstrap is deferred until OpenAlgo, Nautilus, or the paper session is healthy. Resume the agent
          once <span className="font-mono">trade status</span> shows green.
        </p>
        {infraPending && (
          <p className="rounded-lg border border-amber-500/20 bg-background/60 px-3 py-2 text-xs text-amber-900 dark:text-amber-100">
            Pending: {infraPending}
          </p>
        )}
      </div>
    );
  }

  if (isBootstrapping) {
    return (
      <div className="mx-auto max-w-lg space-y-3 rounded-xl border border-primary/30 bg-primary/5 px-4 py-5 text-sm">
        <div className="flex items-center gap-2 font-medium text-primary">
          <Loader2 className={cn("h-4 w-4 shrink-0 animate-spin")} />
          Bootstrapping agent
        </div>
        <p className="text-muted-foreground">
          Running the first watch tick and research turn. Decisions, watch summaries, and tool activity will
          appear here as they complete.
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-lg space-y-3 py-8 text-center text-sm">
      <div className="inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/5 px-3 py-1 text-xs font-medium text-primary">
        <Radio className="h-3.5 w-3.5" />
        Autonomous agent session
      </div>
      <p className="text-muted-foreground">
        No messages yet. Scheduled watch and research turns will post updates here, or send guidance anytime.
      </p>
    </div>
  );
}
