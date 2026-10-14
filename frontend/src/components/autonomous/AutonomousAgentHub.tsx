import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { OctagonX, Plus, Radio, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  ApiError,
  api,
  autonomousDeleteErrorMessage,
  errorText,
  type AutonomousAgentInstance,
  type AutonomousStackHealth,
  type ClearAllAutonomousAgentsResponse,
  type LiveStatus,
  type UnownedPosition,
} from "@/lib/api";
import { useLiveStatus } from "@/hooks/useLiveStatus";
import { AutonomousAgentCard } from "@/components/autonomous/AutonomousAgentCard";
import { haltDetail, ResumeHaltButton } from "@/components/common/ResumeHaltButton";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

const POLL_MS = 15_000;
const REFRESH_DEBOUNCE_MS = 300;

function GlobalHaltBanner({ halt, onResumed }: { halt: LiveStatus["halt"]; onResumed: () => void }) {
  const { t } = useTranslation();
  const [resuming, setResuming] = useState(false);

  const handleResume = async () => {
    if (resuming) return;
    setResuming(true);
    try {
      await api.resumeLive();
      toast.success("Live trading resumed");
      onResumed();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to resume live trading");
    } finally {
      setResuming(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
      <OctagonX className="h-3.5 w-3.5 shrink-0" />
      <span className="font-medium">Live broker halted</span>
      <span className="text-destructive/80">
        Agent controls below won't have any broker-side effect until the global kill switch is cleared.
      </span>
      <span className="text-[11px] text-destructive/80" data-testid="halt-detail">
        {haltDetail(halt ?? null, t as never)}
      </span>
      <span className="ml-auto flex flex-wrap items-center gap-2">
        <ResumeHaltButton resuming={resuming} label="Resume live trading" onConfirm={handleResume} />
      </span>
    </div>
  );
}

export function StackHealthStrip({ health }: { health: AutonomousStackHealth | undefined }) {
  if (!health) return null;
  const sched = health.scheduler_health ?? "unknown";
  const nautilusOn = health.nautilus_watch_enabled !== false;
  const nautilusState = health.nautilus_state;
  const nautilusLabel =
    !nautilusOn
      ? "off"
      : nautilusState === "node_on"
        ? "running"
        : nautilusState === "poll_ok"
          ? "poll"
          : nautilusState === "stale"
            ? "stale"
            : "expected";

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
      <span className="font-medium text-foreground/80">Trader vs infra</span>
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
          (nautilusState === "node_on" || nautilusState === "poll_ok") &&
            "border-emerald-500/40 text-emerald-700",
          nautilusState === "expected" && "text-muted-foreground",
          nautilusState === "stale" && "border-amber-500/40 text-amber-700",
        )}
      >
        nautilus {nautilusLabel}
      </span>
      {health.nautilus_bound_agent_id && (
        <span className="text-muted-foreground">bound {health.nautilus_bound_agent_id}</span>
      )}
      {health.market_open != null && (
        <span>market {health.market_open ? "open" : "closed"}</span>
      )}
      {health.paper_session_enabled && <span>paper session active</span>}
      <span
        className={cn(
          "rounded border px-1.5 py-0.5",
          health.agent_trading_enabled
            ? "border-emerald-500/40 text-emerald-700"
            : "text-muted-foreground",
        )}
      >
        trading {health.agent_trading_enabled ? "live" : "off"}
      </span>
      {health.can_trade !== true && health.cannot_trade_reason && (
        <span
          className={cn(
            "rounded border px-1.5 py-0.5",
            health.can_trade === false
              ? "border-red-500/40 text-red-700"
              : "border-amber-500/40 text-amber-700",
          )}
        >
          cannot trade:{" "}
          {health.cannot_trade_reason === "broker_session_dead"
            ? "broker session dead — log in to the broker in the OpenAlgo UI; INDmoney needs its daily token pasted"
            : health.cannot_trade_reason === "openalgo_unreachable"
              ? "OpenAlgo is not responding — check the stack (trade release status); this is not a token problem"
              : health.cannot_trade_reason}
        </span>
      )}
    </div>
  );
}

export interface ClearAllReport {
  clean: boolean;
  removed: number;
  /** One line per thing that did not go cleanly; empty when `clean`. */
  problems: string[];
  /** Still open on the broker book, owned by no agent: reported, never closed by clear-all. */
  unowned: string[];
}

function positionLabel(p: UnownedPosition): string {
  const qty = p.quantity ?? p.netqty;
  return `${p.symbol ?? p.tradingsymbol ?? "unknown symbol"}${p.exchange ? ` (${p.exchange})` : ""}${
    qty != null ? ` x ${qty}` : ""
  }${p.product ? ` ${p.product}` : ""}`;
}

/** Turn the clear-all response into what the operator must see. `status: "ok"` is the only clean
 * answer; a "partial" status, a per-agent flatten that did not finish, a stop/delete error, a
 * Nautilus error, an unreadable book or any leftover position is each spelled out. */
export function summarizeClearAll(result: ClearAllAutonomousAgentsResponse): ClearAllReport {
  const problems: string[] = [];
  for (const e of result.errors ?? []) problems.push(`${e.agent_id} ${e.phase}: ${e.error}`);
  for (const [agentId, f] of Object.entries(result.flatten ?? {})) {
    if (f && f.status && f.status !== "ok") {
      problems.push(
        `${agentId} flatten ${f.status}${f.error ? `: ${f.error}` : ""}${
          f.remaining_positions ? ` (${f.remaining_positions} position(s) remain)` : ""
        }`,
      );
    }
  }
  if (result.remaining_count > 0) problems.push(`${result.remaining_count} agent(s) were not deleted`);
  if (result.nautilus?.error) problems.push(`Nautilus watch: ${String(result.nautilus.error)}`);
  if (result.book_error) {
    problems.push(`Broker position book unreadable (${result.book_error}) - open positions are UNKNOWN, not flat`);
  }
  const unowned = (result.unowned_positions ?? []).map(positionLabel);
  if (result.status !== "ok" && problems.length === 0) problems.push(`status ${result.status}`);
  return {
    clean: result.status === "ok" && problems.length === 0 && unowned.length === 0,
    removed: result.deleted?.length ?? 0,
    problems,
    unowned,
  };
}

function ClearAllReportPanel({ report, onDismiss }: { report: ClearAllReport; onDismiss: () => void }) {
  if (report.clean) return null;
  return (
    <div
      role="alert"
      data-testid="clear-all-report"
      className="space-y-2 rounded-lg border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-200"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">
          Clear all removed {report.removed} agent(s) but did not finish cleanly
        </span>
        <button type="button" onClick={onDismiss} className="rounded border border-amber-500/40 px-2 py-0.5">
          Dismiss
        </button>
      </div>
      {report.problems.length > 0 && (
        <ul className="list-disc pl-4">
          {report.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      {report.unowned.length > 0 && (
        <div>
          <p className="font-medium">
            {report.unowned.length} position(s) still open on the broker, owned by no agent. Clear all does
            not close them - close them yourself in OpenAlgo:
          </p>
          <ul className="list-disc pl-4" data-testid="clear-all-unowned">
            {report.unowned.map((p, i) => (
              <li key={`${p}-${i}`}>{p}</li>
            ))}
          </ul>
        </div>
      )}
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
  const [clearing, setClearing] = useState(false);
  // The one poller of GET /live/status (D306), with its latest-wins and stale-error handling.
  const { liveStatus, error: liveStatusError, refresh: refreshLiveStatus } = useLiveStatus();
  const [clearReport, setClearReport] = useState<ClearAllReport | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await api.listAutonomousAgents();
      const rows = Array.isArray(res.agents) ? res.agents : [];
      rows.sort((a, b) => {
        if (a.status === "draft" && b.status !== "draft") return -1;
        if (b.status === "draft" && a.status !== "draft") return 1;
        return 0;
      });
      setAgents(rows);
      setStackHealth(res.stack_health);
      setLoadError(null);
    } catch (err) {
      console.warn("Failed to load autonomous agents", err);
      setLoadError(err instanceof Error ? err.message : "Failed to load agents.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, POLL_MS);
    let debounceTimer: number | undefined;
    const onRefresh = () => {
      window.clearTimeout(debounceTimer);
      debounceTimer = window.setTimeout(() => void load(), REFRESH_DEBOUNCE_MS);
    };
    window.addEventListener("autonomous-agents-refresh", onRefresh);
    return () => {
      window.clearInterval(timer);
      window.clearTimeout(debounceTimer);
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
      const result = await api.resumeAutonomousAgent(id);
      if (result.halt_cleared) toast.success(`Trading halt lifted (was: ${result.halt_cleared})`);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Resume failed");
    }
  };

  const confirmFlattenDelete = (_agent: AutonomousAgentInstance, prompt: string) => {
    if (!window.confirm(prompt)) return false;
    return window.confirm(
      "This will flatten agent-scoped positions before delete. Continue?",
    );
  };

  const handleDelete = async (agent: AutonomousAgentInstance) => {
    const isDraft = agent.status === "draft";
    const prompt = isDraft
      ? "Delete this draft? Chat and proposal will be removed."
      : "Delete this autonomous agent?";
    if (!window.confirm(prompt)) return;
    try {
      await api.deleteAutonomousAgent(agent.id);
      await load();
      toast.success(isDraft ? "Draft deleted" : "Agent deleted");
    } catch (err) {
      if (err instanceof ApiError && (err.status === 409 || err.status === 503)) {
        const detail = (err.detail ?? {}) as Record<string, unknown>;
        const needsFlatten =
          err.status === 409
            ? detail.error === "open_positions" || detail.error === "flatten_incomplete"
            : detail.error === "position_lookup_failed";
        if (needsFlatten) {
          const flattenPrompt =
            detail.error === "flatten_incomplete"
              ? `${err.message}\n\nTry delete and flatten again?`
              : detail.error === "position_lookup_failed"
                ? `${err.message}\n\nDelete and flatten anyway?`
                : `${err.message}\n\nDelete and flatten positions?`;
          if (confirmFlattenDelete(agent, flattenPrompt)) {
            try {
              await api.deleteAutonomousAgent(agent.id, { flattenPositions: true });
              await load();
              toast.success("Agent deleted and positions flattened");
            } catch (flattenErr) {
              toast.error(autonomousDeleteErrorMessage(flattenErr));
            }
          }
          return;
        }
      }
      toast.error(autonomousDeleteErrorMessage(err));
    }
  };

  const handleClearAll = async () => {
    const count = agents.length;
    const hasInfra =
      stackHealth?.paper_session_enabled ||
      stackHealth?.nautilus_bound_agent_id ||
      (stackHealth?.nautilus_state && stackHealth.nautilus_state !== "expected");
    if (count === 0 && !hasInfra) {
      toast.message("Nothing to clear");
      return;
    }
    const prompt =
      count > 0
        ? `Clear all ${count} autonomous agent(s)? This will flatten open positions, stop Nautilus watch, and remove all agents and bridge artifacts.`
        : "Clear autonomous infrastructure (Nautilus watch, paper session, bridge artifacts)?";
    if (!window.confirm(prompt)) return;

    setClearing(true);
    try {
      const result = await api.clearAllAutonomousAgents();
      await load();
      const report = summarizeClearAll(result);
      setClearReport(report);
      if (report.clean) {
        toast.success(
          report.removed > 0
            ? `Cleared ${report.removed} agent(s) and stopped Nautilus watch`
            : "Autonomous infrastructure cleared",
        );
      } else {
        toast.warning(`Cleared ${report.removed} agent(s) with problems - see the report below`);
      }
    } catch (err) {
      toast.error(errorText(err, "Clear all failed"));
    } finally {
      setClearing(false);
    }
  };

  const canClearAll =
    agents.length > 0 ||
    stackHealth?.paper_session_enabled ||
    Boolean(stackHealth?.nautilus_bound_agent_id) ||
    (stackHealth?.nautilus_state != null && stackHealth.nautilus_state !== "expected");

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Autonomous</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Persistent trading agents — Nautilus watches, Vibe decides, OpenAlgo executes.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void handleClearAll()}
          disabled={loading || clearing || !canClearAll}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors",
            canClearAll
              ? "border-red-500/40 text-red-700 hover:bg-red-500/10"
              : "border-border text-muted-foreground",
            (loading || clearing) && "opacity-60",
          )}
        >
          <Trash2 className="h-3.5 w-3.5" />
          {clearing ? "Clearing…" : "Clear all agents"}
        </button>
      </div>

      {liveStatus?.global_halted && (
        <GlobalHaltBanner halt={liveStatus.halt} onResumed={() => void refreshLiveStatus()} />
      )}

      {liveStatusError && (
        <div
          role="alert"
          data-testid="live-status-error"
          className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-200"
        >
          Live-broker halt state unknown ({liveStatusError}) - do not assume trading is not halted.
        </div>
      )}

      {clearReport && <ClearAllReportPanel report={clearReport} onDismiss={() => setClearReport(null)} />}

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

        {!loading && loadError && (
          <div className="col-span-full flex flex-col items-start gap-2 rounded-xl border border-red-500/40 bg-red-500/5 p-4 text-sm">
            <span className="text-red-600 dark:text-red-400">{loadError}</span>
            <button
              type="button"
              onClick={() => void load()}
              className="rounded-lg border px-3 py-1.5 text-xs font-medium"
            >
              Retry
            </button>
          </div>
        )}

        {!loading && !loadError && agents.length === 0 && (
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
            onResume={
              agent.status === "paused" || agent.trading_halted ? () => handleResume(agent.id) : undefined
            }
            onDelete={() => void handleDelete(agent)}
          />
        ))}
      </div>
    </div>
  );
}
