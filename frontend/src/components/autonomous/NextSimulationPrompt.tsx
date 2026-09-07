import { useCallback, useEffect, useState } from "react";
import { Flag, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type {
  AutonomousAgentInstance,
  NextSimulationChoice,
  SimulationPromptState,
} from "@/lib/api";

interface Props {
  agent: AutonomousAgentInstance;
  onStarted?: (agentId: string, sessionId: string) => void;
  onRefresh?: () => void;
}

/**
 * "Run the next simulation?" — shown only when a replay pass has ended.
 *
 * A pass over the replay window is one simulation, and it stops the agent terminally when it
 * wraps (`stop_reason === "simulation_complete"`). Continuing is a human act, and it is a
 * choice of two: the change under test is often outside the agent config entirely — other
 * code, an added knowledge base — in which case re-running the *identical* configuration is
 * the correct experiment. Declining keeps every record for comparison; nothing here archives
 * or deletes anything.
 *
 * Deliberately does not appear for a paused agent of any kind. A restart force-pauses every
 * running agent (`pause_reason === "restart"`), and that agent is resumed, not re-run — the
 * backend gates this on `simulation_complete`, and so does the render below.
 *
 * See docs/add/autonomous_agents.md § "Simulation runs".
 */
export function NextSimulationPrompt({ agent, onStarted, onRefresh }: Props) {
  const [state, setState] = useState<SimulationPromptState | null>(null);
  const [busy, setBusy] = useState<NextSimulationChoice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [prefill, setPrefill] = useState<Record<string, unknown> | null>(null);

  const eligible = agent.status === "stopped";

  useEffect(() => {
    if (!eligible) {
      setState(null);
      return;
    }
    let cancelled = false;
    api
      .getSimulationState(agent.id)
      .then((next) => {
        if (!cancelled) setState(next);
      })
      .catch(() => {
        // A missing endpoint (an older release tier) must not break the agent view.
        if (!cancelled) setState(null);
      });
    return () => {
      cancelled = true;
    };
  }, [agent.id, eligible]);

  const answer = useCallback(
    async (choice: NextSimulationChoice) => {
      setBusy(choice);
      setError(null);
      try {
        const result = await api.answerNextSimulation(agent.id, {
          configuration: choice,
          consent_ack: choice === "same_configuration",
        });
        if (result.action === "started" && result.agent?.id && result.vibe_session_id) {
          onStarted?.(result.agent.id, result.vibe_session_id);
        } else if (result.action === "prefill") {
          setPrefill(result.config ?? {});
        }
        const next = await api.getSimulationState(agent.id);
        setState(next);
        onRefresh?.();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(null);
      }
    },
    [agent.id, onRefresh, onStarted],
  );

  if (!state?.prompt_pending) return null;

  return (
    <div className="mx-4 mt-3 rounded-xl border border-primary/40 bg-primary/5 p-4 text-sm">
      <div className="flex items-center gap-2 font-semibold text-foreground">
        <Flag className="h-4 w-4 text-primary" />
        Simulation complete
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        This agent finished its replay window
        {state.completed_run_id ? ` (${state.completed_run_id})` : ""} and has stopped. Stopping is
        final — a stopped simulation is not resumed, it is re-run as a new agent. Its records are
        kept either way, for comparison against other simulations.
      </p>
      <p className="mt-2 text-xs text-muted-foreground">Run the next simulation?</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => void answer("same_configuration")}
          className="inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
        >
          {busy === "same_configuration" && <Loader2 className="h-3 w-3 animate-spin" />}
          Same configuration
        </button>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => void answer("new_configuration")}
          className="inline-flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs font-medium disabled:opacity-50"
        >
          {busy === "new_configuration" && <Loader2 className="h-3 w-3 animate-spin" />}
          New configuration
        </button>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => void answer("decline")}
          className="inline-flex items-center gap-1 rounded-lg px-3 py-1.5 text-xs text-muted-foreground disabled:opacity-50"
        >
          {busy === "decline" && <Loader2 className="h-3 w-3 animate-spin" />}
          No, stop here
        </button>
      </div>
      {prefill && (
        <p className="mt-3 text-xs text-muted-foreground">
          Start a new agent and edit the configuration — the previous one was:{" "}
          <code className="rounded bg-muted px-1">{JSON.stringify(prefill)}</code>
        </p>
      )}
      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
    </div>
  );
}
