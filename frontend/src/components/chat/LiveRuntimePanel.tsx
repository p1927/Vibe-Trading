import {
  createContext,
  forwardRef,
  memo,
  useCallback,
  useContext,
  useEffect,
  useImperativeHandle,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { Activity, Ban, CheckCircle2, Loader2, OctagonX, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import {
  api,
  type LiveAction,
  type LiveHaltRecord,
  type LiveHalted,
  type LiveStatus,
  type TradingConnectorsResponse,
} from "@/lib/api";
import { haltScopeStillActive, isGlobalLiveHalt, useLiveStatus } from "@/hooks/useLiveStatus";
import { AgentAvatar } from "@/components/chat/AgentAvatar";
import { RunnerStatus, type ConnectorCheckSnapshot } from "@/components/chat/RunnerStatus";

function liveActionStyle(kind: string): { icon: typeof Activity; tone: string } {
  switch (kind) {
    case "order_rejected":
    case "breach":
      return { icon: Ban, tone: "border-amber-500/40 bg-amber-500/5 text-amber-600 dark:text-amber-400" };
    case "halt_tripped":
      return { icon: OctagonX, tone: "border-destructive/40 bg-destructive/5 text-destructive" };
    case "mandate_committed":
    case "halt_cleared":
      return { icon: CheckCircle2, tone: "border-emerald-500/40 bg-emerald-500/5 text-emerald-600 dark:text-emerald-400" };
    default:
      return { icon: Activity, tone: "border-sky-500/40 bg-sky-500/5 text-sky-600 dark:text-sky-400" };
  }
}

function liveActionLabel(action: LiveAction): string {
  return action.kind.replace(/_/g, " ");
}

export const LiveActionChip = memo(function LiveActionChip({ action }: { action: LiveAction }) {
  const { t } = useTranslation();
  const { icon: Icon, tone } = liveActionStyle(action.kind);
  return (
    <div className="flex gap-3">
      <AgentAvatar />
      <div className="flex-1 min-w-0">
        <div className={["inline-flex max-w-full flex-wrap items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs", tone].join(" ")}>
          <Icon className="h-3 w-3 shrink-0" />
          <span className="shrink-0 font-medium uppercase tracking-wide text-[10px]">{t("agent.runtimeBadge")}</span>
          <span className="shrink-0 font-medium">{liveActionLabel(action)}</span>
          {action.intent_normalized && (
            <span className="truncate text-foreground/80">· {action.intent_normalized}</span>
          )}
          {action.outcome && (
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">· {action.outcome}</span>
          )}
          {action.remote_tool && (
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">· {action.remote_tool}</span>
          )}
          {action.error && <span className="truncate text-destructive">· {action.error}</span>}
        </div>
      </div>
    </div>
  );
});

export interface LiveRuntimePanelHandle {
  resetSession(): void;
  handleMandateCommitted(): void;
  handleHalted(halted: LiveHalted): void;
  handleResumed(): void;
  handleLiveAction(action: LiveAction): void;
}

interface RuntimeContextValue {
  liveStatus: LiveStatus | null;
  liveStatusUnavailable: boolean;
  liveStatusError: string | null;
  connectors: TradingConnectorsResponse | null;
  connectorCheck: ConnectorCheckSnapshot | null;
  /** The OAuth live runtime is in play (selected remote-MCP profile, or a broker holds a token/runner/mandate). */
  showOAuthRuntime: boolean;
  liveActive: boolean;
  /** A global kill switch is in force (from `/live/status` or a live event) regardless of the selected connector. */
  liveIsHalted: boolean;
  /** Who/why/when, from the sentinel (`/live/status`) or the `live.halted` event. */
  haltRecord: LiveHaltRecord | null;
  halting: boolean;
  resuming: boolean;
  refreshLiveStatus: () => Promise<void>;
  haltLive: () => Promise<void>;
  resumeLive: () => Promise<void>;
}

const RuntimeContext = createContext<RuntimeContextValue | null>(null);

interface Props {
  sessionId: string | null;
  hasLiveItems: boolean;
  connectors?: TradingConnectorsResponse | null;
  connectorCheck?: ConnectorCheckSnapshot | null;
  /** Refreshed on the same tick as `/live/status` (the Agent page's connector list). */
  alongside?: () => Promise<unknown>;
  children: ReactNode;
}

export const LiveRuntimePanel = memo(forwardRef<LiveRuntimePanelHandle, Props>(
  function LiveRuntimePanel({
    sessionId,
    hasLiveItems,
    connectors = null,
    connectorCheck = null,
    alongside,
    children,
  }, ref) {
    const { t } = useTranslation();
    const [hasCommittedMandate, setHasCommittedMandate] = useState(false);
    const [liveHalted, setLiveHalted] = useState<LiveHalted | null>(null);
    const [halting, setHalting] = useState(false);
    const [resuming, setResuming] = useState(false);
    const [liveStatusRefresh, setLiveStatusRefresh] = useState(0);
    const {
      liveStatus,
      unavailable: liveStatusUnavailable,
      error: liveStatusError,
      refresh: refreshLiveStatus,
    } = useLiveStatus({
      refreshKey: liveStatusRefresh,
      alongside,
      onSnapshot: (next) => setLiveHalted((current) => (
        current && !haltScopeStillActive(current, next) ? null : current
      )),
    });

    useImperativeHandle(ref, () => ({
      resetSession() {
        setHasCommittedMandate(false);
        setLiveHalted(null);
        setLiveStatusRefresh((value) => value + 1);
      },
      handleMandateCommitted() {
        setHasCommittedMandate(true);
        setLiveStatusRefresh((value) => value + 1);
      },
      handleHalted(halted: LiveHalted) {
        setLiveHalted(halted);
        setLiveStatusRefresh((value) => value + 1);
      },
      handleResumed() {
        setLiveHalted(null);
        setLiveStatusRefresh((value) => value + 1);
      },
      handleLiveAction(action: LiveAction) {
        if (action.kind === "mandate_committed") setHasCommittedMandate(true);
        if (action.kind === "halt_tripped") {
          setLiveHalted({ broker: action.broker, reason: action.intent_normalized });
        }
        if (action.kind === "halt_cleared") setLiveHalted(null);
        if (["mandate_committed", "halt_tripped", "halt_cleared"].includes(action.kind)) {
          setLiveStatusRefresh((value) => value + 1);
        }
      },
    }), []);

    const haltLive = useCallback(async () => {
      if (halting) return;
      setHalting(true);
      try {
        await api.haltLive(sessionId ?? undefined);
        setLiveHalted((current) => current ?? {
          broker: null,
          by: "frontend",
          tripped_at: new Date().toISOString(),
        });
        setLiveStatusRefresh((value) => value + 1);
        toast.success(t("agent.connectorHalted"));
      } catch (error) {
        toast.error(error instanceof Error ? error.message : t("agent.failedToHaltConnector"));
      } finally {
        setHalting(false);
      }
    }, [halting, sessionId, t]);

    const resumeLive = useCallback(async () => {
      if (resuming) return;
      setResuming(true);
      try {
        await api.resumeLive(sessionId ?? undefined);
        setLiveHalted(null);
        setLiveStatusRefresh((value) => value + 1);
        toast.success(t("agent.connectorResumed"));
      } catch (error) {
        toast.error(error instanceof Error ? error.message : t("agent.failedToResumeConnector"));
      } finally {
        setResuming(false);
      }
    }, [resuming, sessionId, t]);

    const oauthRuntimeEngaged = liveStatus != null && liveStatus.brokers.some((broker) => (
      broker.auth.oauth_token_present || broker.runner?.alive || broker.mandate != null
    ));
    const showOAuthRuntime =
      connectors?.profiles.find((profile) => profile.selected)?.transport === "remote_mcp" || oauthRuntimeEngaged;
    const liveStatusActive =
      liveStatus != null && showOAuthRuntime && (liveStatus.global_halted || oauthRuntimeEngaged);
    const liveActive =
      hasLiveItems ||
      hasCommittedMandate ||
      liveHalted != null ||
      liveStatusActive;
    const liveIsHalted =
      isGlobalLiveHalt(liveHalted) || (liveStatus?.global_halted ?? false);
    const haltRecord: LiveHaltRecord | null = !liveIsHalted
      ? null
      : (liveStatus?.global_halted ? liveStatus.halt ?? null : null) ?? (liveHalted
        ? { by: liveHalted.by ?? null, reason: liveHalted.reason ?? null, tripped_at: liveHalted.tripped_at ?? null }
        : null);

    const value = useMemo<RuntimeContextValue>(() => ({
      liveStatus,
      liveStatusUnavailable,
      liveStatusError,
      connectors,
      connectorCheck,
      showOAuthRuntime,
      liveActive,
      liveIsHalted,
      haltRecord,
      halting,
      resuming,
      refreshLiveStatus,
      haltLive,
      resumeLive,
    }), [
      connectorCheck,
      connectors,
      haltLive,
      halting,
      haltRecord,
      liveActive,
      liveIsHalted,
      showOAuthRuntime,
      liveStatus,
      liveStatusUnavailable,
      liveStatusError,
      refreshLiveStatus,
      resumeLive,
      resuming,
    ]);

    return (
      <RuntimeContext.Provider value={value}>
        {children}
      </RuntimeContext.Provider>
    );
  },
));

export function LiveRuntimeStatus() {
  const runtime = useContext(RuntimeContext);
  if (!runtime) return null;
  return (
    <RunnerStatus
      status={runtime.liveStatus}
      connectors={runtime.connectors}
      connectorCheck={runtime.connectorCheck}
      unavailable={runtime.liveStatusUnavailable}
      staleError={runtime.liveStatusError}
      halted={runtime.showOAuthRuntime && runtime.liveIsHalted}
      onRefresh={runtime.refreshLiveStatus}
    />
  );
}

function haltDetail(record: LiveHaltRecord | null, t: (key: never, options?: Record<string, unknown>) => string): string {
  const parts: string[] = [];
  if (record?.by) parts.push(t("agent.haltTrippedBy" as never, { by: record.by }));
  if (record?.reason) parts.push(t("agent.haltTrippedReason" as never, { reason: record.reason }));
  if (record?.tripped_at) {
    const when = new Date(record.tripped_at);
    parts.push(t("agent.haltTrippedAt" as never, {
      at: Number.isNaN(when.getTime()) ? record.tripped_at : when.toLocaleString(),
    }));
  }
  return parts.length > 0 ? parts.join(" · ") : t("agent.haltAttributionUnknown" as never);
}

/**
 * Halt / Resume. A halt (kill switch, risk engine, daily loss, manual) is only ever lifted by a human:
 * Resume asks for a second, explicit confirmation that shows who/why/when it was tripped. Nothing in the
 * UI clears a halt on its own (D306: this is the one path).
 */
export function LiveRuntimeControl() {
  const { t } = useTranslation();
  const runtime = useContext(RuntimeContext);
  const [confirming, setConfirming] = useState(false);
  const halted = runtime?.liveIsHalted ?? false;
  useEffect(() => {
    if (!halted) setConfirming(false);
  }, [halted]);
  if (!runtime || !(runtime.liveIsHalted || (runtime.liveActive && runtime.showOAuthRuntime))) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-border/60 pt-2">
      {runtime.liveIsHalted ? (
        <>
          <span
            role="alert"
            className="inline-flex items-center gap-1.5 rounded-lg bg-destructive/10 px-2.5 py-1 text-xs font-medium text-destructive"
          >
            <OctagonX className="h-3 w-3" />
            {t("agent.connectorHalted")}
          </span>
          <span className="text-[11px] text-muted-foreground" data-testid="halt-detail">
            {haltDetail(runtime.haltRecord, t as never)}
          </span>
          {confirming ? (
            <>
              <span className="text-xs text-foreground">{t("agent.resumeConfirmPrompt" as never)}</span>
              <button
                type="button"
                onClick={() => { setConfirming(false); void runtime.resumeLive(); }}
                disabled={runtime.resuming}
                className="inline-flex items-center gap-1.5 rounded-lg border border-destructive/40 bg-destructive/5 px-2.5 py-1 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-40"
              >
                {runtime.resuming ? <Loader2 className="h-3 w-3 animate-spin" /> : <RotateCcw className="h-3 w-3" />}
                {t("agent.resumeConfirm" as never)}
              </button>
              <button
                type="button"
                onClick={() => setConfirming(false)}
                className="inline-flex items-center rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                {t("agent.resumeKeepHalted" as never)}
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={() => setConfirming(true)}
              disabled={runtime.resuming}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
              title={t("agent.resumeConnectorTitle")}
            >
              <RotateCcw className="h-3 w-3" />
              {t("agent.resumeConnector")}
            </button>
          )}
        </>
      ) : (
        <button
          type="button"
          onClick={runtime.haltLive}
          disabled={runtime.halting}
          className="inline-flex items-center gap-1.5 rounded-lg border border-destructive/40 bg-destructive/5 px-2.5 py-1 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-40"
          title={t("agent.haltConnectorTitle")}
        >
          {runtime.halting
            ? <Loader2 className="h-3 w-3 animate-spin" />
            : <OctagonX className="h-3 w-3" />}
          {t("agent.haltConnector")}
        </button>
      )}
    </div>
  );
}
