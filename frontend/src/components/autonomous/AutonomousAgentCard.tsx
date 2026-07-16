import { useState } from "react";
import { ChevronDown, ChevronUp, Loader2, Radio } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AutonomousAgentInstance } from "@/lib/api";

function statusColor(status: string): string {
  switch (status) {
    case "running":
      return "bg-emerald-500";
    case "paused":
      return "bg-amber-500";
    case "halted":
    case "stopped":
      return "bg-muted-foreground";
    default:
      return "bg-primary";
  }
}

function schedulerChip(state: string | undefined): string {
  switch (state) {
    case "ok":
      return "scheduler ok";
    case "initializing":
      return "initializing";
    case "bootstrap_failed":
      return "bootstrap failed";
    case "stale":
      return "scheduler stale";
    case "disabled":
      return "scheduler off";
    default:
      return "scheduler —";
  }
}

function nautilusChip(state: string | undefined, enabled: boolean | undefined): string {
  if (enabled === false) return "nautilus off";
  switch (state) {
    case "node_on":
      return "nautilus ok";
    case "poll_ok":
      return "nautilus poll";
    case "expected":
      return "nautilus expected";
    case "stale":
      return "nautilus stale";
    default:
      return "nautilus stale";
  }
}

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return "just now";
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
  return `${Math.floor(ms / 3_600_000)}h ago`;
}

interface Props {
  agent: AutonomousAgentInstance;
  onOpen: () => void;
  onPause?: () => void;
  onResume?: () => void;
  onDelete?: () => void;
}

export function AutonomousAgentCard({ agent, onOpen, onPause, onResume, onDelete }: Props) {
  const [expanded, setExpanded] = useState(false);
  const confidence = agent.thesis?.confidence;
  const runtime = agent.runtime;
  const mandate = runtime?.mandate_summary;
  const lastDecision = (runtime?.last_decision || agent.last_decision) as { decision?: string } | null;

  const schedState = runtime?.scheduler_health ?? "unknown";
  const nautilusState = runtime?.nautilus_state;
  const isBootstrapping =
    agent.streaming ||
    schedState === "initializing" ||
    agent.bootstrap_status === "pending" ||
    agent.bootstrap_status === "running" ||
    runtime?.bootstrap_status === "pending" ||
    runtime?.bootstrap_status === "running";
  const bootstrapFailed =
    schedState === "bootstrap_failed" ||
    agent.bootstrap_status === "failed" ||
    runtime?.bootstrap_status === "failed";

  return (
    <div
      className={cn(
        "rounded-xl border bg-card p-4 shadow-sm transition-colors hover:border-primary/40",
        isBootstrapping && "ring-1 ring-primary/30",
      )}
    >
      <button type="button" onClick={onOpen} className="w-full text-left">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className={cn("h-2 w-2 shrink-0 rounded-full", statusColor(agent.status))} />
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-foreground">{agent.name}</p>
              <p className="truncate text-xs text-muted-foreground">
                {agent.symbols.join(" · ")} · {agent.status}
                {bootstrapFailed ? " · bootstrap failed" : isBootstrapping ? " · starting" : ""}
              </p>
            </div>
          </div>
          {isBootstrapping ? (
            <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
          ) : (
            <Radio className="h-4 w-4 shrink-0 text-muted-foreground" />
          )}
        </div>

        {mandate?.holding_period && (
          <div className="mt-2 flex flex-wrap gap-1">
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
              {mandate.holding_period.replace("_", " ")}
            </span>
            {mandate.flatten_policy && (
              <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                flatten: {mandate.flatten_policy.replace("_", " ")}
              </span>
            )}
            {mandate.allowed_instruments?.length ? (
              <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {mandate.allowed_instruments.join(", ")}
              </span>
            ) : null}
          </div>
        )}

        <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
          {confidence != null && <span>conf {confidence}%</span>}
          {lastDecision?.decision && (
            <span className="font-medium text-foreground/80">last {lastDecision.decision}</span>
          )}
          <span>watch {relativeTime(agent.last_watch_at)}</span>
          {agent.last_revision_at && <span>revise {relativeTime(agent.last_revision_at)}</span>}
        </div>

        <div className="mt-1.5 flex flex-wrap gap-1.5 text-[10px]">
          <span
            className={cn(
              "rounded border px-1.5 py-0.5",
              schedState === "ok" && "border-emerald-500/40 text-emerald-600",
              schedState === "initializing" && "border-primary/40 text-primary",
              schedState === "bootstrap_failed" && "border-red-500/40 text-red-600",
              schedState === "stale" && "border-amber-500/40 text-amber-600",
              schedState === "disabled" && "text-muted-foreground",
            )}
          >
            {schedulerChip(schedState)}
          </span>
          <span
            className={cn(
              "rounded border px-1.5 py-0.5",
              nautilusState === "node_on" && "border-emerald-500/40 text-emerald-600",
              nautilusState === "poll_ok" && "border-emerald-500/40 text-emerald-600",
              nautilusState === "expected" && "border-muted text-muted-foreground",
              nautilusState === "stale" && "border-amber-500/40 text-amber-600",
              nautilusState !== "node_on" &&
                nautilusState !== "poll_ok" &&
                nautilusState !== "expected" &&
                nautilusState !== "stale" &&
                runtime?.nautilus_watch_enabled !== false &&
                "border-amber-500/40 text-amber-600",
              runtime?.nautilus_watch_enabled === false && "text-muted-foreground",
            )}
          >
            {nautilusChip(nautilusState, runtime?.nautilus_watch_enabled)}
          </span>
          {runtime?.watch_configured && !runtime?.position_tracked && (
            <span className="rounded border border-muted px-1.5 py-0.5 text-muted-foreground">watch ready</span>
          )}
          {runtime?.position_tracked && (
            <span className="rounded border border-primary/30 px-1.5 py-0.5 text-primary">position tracked</span>
          )}
        </div>
      </button>

      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="mt-2 flex w-full items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
      >
        {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        {expanded ? "Less" : "More"}
      </button>

      {expanded && (
        <div className="mt-2 space-y-2 border-t border-border/60 pt-2 text-[11px]">
          {bootstrapFailed && agent.bootstrap_error && (
            <p className="text-red-600/90">{agent.bootstrap_error}</p>
          )}
          {agent.thesis?.rationale && (
            <p className="text-muted-foreground line-clamp-3">{agent.thesis.rationale}</p>
          )}
          <p className="text-muted-foreground">{agent.mandate}</p>
          {runtime?.market_open != null && (
            <p className="text-muted-foreground">
              Market {runtime.market_open ? "open" : "closed"}
              {runtime.open_positions != null ? ` · ${runtime.open_positions} open position(s)` : ""}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            {agent.status === "running" && onPause && (
              <button type="button" onClick={onPause} className="rounded border px-2 py-0.5 hover:bg-muted">
                Pause
              </button>
            )}
            {agent.status === "paused" && onResume && (
              <button type="button" onClick={onResume} className="rounded border px-2 py-0.5 hover:bg-muted">
                Resume
              </button>
            )}
            {onDelete && (
              <button
                type="button"
                onClick={onDelete}
                className="rounded border border-destructive/40 px-2 py-0.5 text-destructive hover:bg-destructive/10"
              >
                Delete
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
