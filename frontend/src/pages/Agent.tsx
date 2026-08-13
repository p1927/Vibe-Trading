import { useTranslation } from 'react-i18next';
import { useEffect, useRef, useState, useMemo, useCallback, type FormEvent } from "react";
import { useSearchParams } from "react-router";
import {
  Activity,
  ArrowDown,
  Ban,
  Check,
  CheckCircle2,
  ChevronDown,
  Download,
  Landmark,
  Loader2,
  OctagonX,
  Paperclip,
  Pencil,
  Play,
  Plus,
  Send,
  Square,
  Target,
  Users,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { useAgentStore, type AgentActivity, type AgentMessageMeta, type StoredAgentMessage } from "@/stores/agent";
import { useProvenanceStore } from "@/stores/provenance";
import { useSSE } from "@/hooks/useSSE";
import { useThrottledValue } from "@/hooks/useThrottledValue";
import { ApiError, AUTH_REQUIRED_MESSAGE, api, isAuthRequiredError, type GoalSnapshot, type MandateProposal, type MandateCommitted, type LiveAction, type AgentAudit, type LiveHalted, type LiveStatus, type TradePlanWidget, type HubPlanArtifact, type AgentDebateArtifact, type ProvenanceSource, type AutonomousAgentProposal, type AutonomousAgentInstance, type TradingConnectorsResponse, type LLMSettings } from "@/lib/api";
import { isReportWorthyRun } from "@/lib/runReports";
import { buildToolTimelineMessages } from "@/pages/agentToolTimeline";
import type { AgentMessage, SwarmRunStatus, ToolCallEntry } from "@/types/agent";
import { AgentAvatar } from "@/components/chat/AgentAvatar";
import { WelcomeScreen } from "@/components/chat/WelcomeScreen";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { MarkdownContent } from "@/components/chat/MarkdownContent";
import { ThinkingTimeline } from "@/components/chat/ThinkingTimeline";
import { ConversationTimeline } from "@/components/chat/ConversationTimeline";
import { ToolProgressIndicator } from "@/components/chat/ToolProgressIndicator";
import { MandateProposalCard } from "@/components/chat/MandateProposalCard";
import { AutonomousAgentProposalCard } from "@/components/autonomous/AutonomousAgentProposalCard";
import { OrchestratorWelcome } from "@/components/autonomous/OrchestratorWelcome";
import { AutonomousSessionEmptyState } from "@/components/autonomous/AutonomousSessionEmptyState";
import { TradePlanWidgetCard } from "@/components/chat/TradePlanWidgetCard";
import { ContextDrawer } from "@/components/research/ContextDrawer";
import { ActivityLine } from "@/components/chat/ActivityLine";
import { ModelRuntimeBar } from "@/components/chat/ModelRuntimeBar";
import type { ComposerHandle } from "@/components/chat/Composer";
import {
  formatTradeWidgetContextBlock,
  getTradeWidgetAdjustment,
  isTradeWidgetModified,
} from "@/lib/tradeWidgetContext";
import { RunnerStatus } from "@/components/chat/RunnerStatus";
import { SwarmStatusCard } from "@/components/chat/SwarmStatusCard";
import {
  applySwarmEvent,
  buildSwarmStatusFromStarted,
  buildSwarmStatusFromToolResultPreview,
} from "@/lib/swarmStatus";

/* ---------- Message grouping ---------- */
type MsgGroup =
  | { kind: "single"; msg: StoredAgentMessage; msgIdx: number }
  | { kind: "timeline"; msgs: StoredAgentMessage[]; msgIdx: number };

function groupMessages(msgs: StoredAgentMessage[]): MsgGroup[] {
  const out: MsgGroup[] = [];
  let buf: StoredAgentMessage[] = [];
  let bufStartIdx = -1;
  const flush = () => {
    if (buf.length) {
      out.push({ kind: "timeline", msgs: [...buf], msgIdx: bufStartIdx });
      buf = [];
      bufStartIdx = -1;
    }
  };
  for (const [msgIdx, m] of msgs.entries()) {
    if (["thinking", "tool_call", "tool_result", "compact"].includes(m.type)) {
      if (bufStartIdx < 0) bufStartIdx = msgIdx;
      buf.push(m);
    } else {
      flush();
      out.push({ kind: "single", msg: m, msgIdx });
    }
  }
  flush();
  return out;
}

interface PendingToolProgress {
  callId?: string;
  tool: string;
  progress: NonNullable<ToolCallEntry["progress"]>;
}

interface RuntimeIdentity {
  sessionId?: string;
  provider?: string;
  model?: string;
  reasoningEffort?: string;
}

function toolProgressKey(callId: string | undefined, tool: string): string {
  return callId ? `call:${callId}` : `tool:${tool}`;
}

function eventCallId(data: Record<string, unknown>): string | undefined {
  return typeof data.call_id === "string" && data.call_id
    ? data.call_id
    : undefined;
}

const act = () => useAgentStore.getState();

// i18n hook for Agent component — used inside the component below
// (declared at module scope for helper usage is fine since t() reads from i18n singleton)

/** Poll cadence for the shared `GET /live/status` snapshot. */
const LIVE_STATUS_POLL_INTERVAL_MS = 15_000;
const STREAM_FLUSH_INTERVAL_MS = 80;
const TIMELINE_WINDOW_SIZE = 160;
const CONNECTOR_CHECK_PROMPT =
  "List my trading connector profiles, show which one is selected, then check that selected connector. If it is not ready, tell me exactly what setup step is missing. Do not place or modify orders.";
const CONNECTOR_PORTFOLIO_PROMPT =
  "Use the selected trading connector profile to summarize my account, positions, concentration, cash, and portfolio risk. Do not place or modify orders.";
const SWARM_PROMPT_PREFIX =
  "[Swarm Team Mode] Use the swarm tool to assemble the best specialist team for this task. Auto-select the most appropriate preset.\n\n";
const GOAL_KICKOFF_PREFIX = [
  "Start working on this research goal now.",
  "Keep it research-only, use available tools when evidence is needed, add concrete evidence to the goal ledger, and keep going until the goal is complete, blocked, waiting for user input, or budget-limited.",
  "",
  "Goal: ",
].join("\n");
const GOAL_CONTINUE_PREFIX = [
  "Continue the active research goal.",
  "Use real available tools as needed, add evidence to the goal ledger, and only stop when the goal is complete, blocked, waiting for user input, or budget-limited.",
  "",
  "Goal: ",
].join("\n");

function toDisplayPrompt(content: string): {
  content: string;
  meta?: AgentMessageMeta;
} {
  let display = content;
  const meta: AgentMessageMeta = { requestText: content };
  const attachmentMatch = display.match(
    /^\[Uploaded file: (.+), path: [^\n]*\]\n\n/,
  );
  if (attachmentMatch) {
    meta.attachment = { filename: attachmentMatch[1] };
    display = display.slice(attachmentMatch[0].length);
  }
  if (display.startsWith(SWARM_PROMPT_PREFIX)) {
    meta.swarmMode = true;
    display = display.slice(SWARM_PROMPT_PREFIX.length);
  }
  if (display.startsWith(GOAL_KICKOFF_PREFIX)) {
    meta.goalMode = true;
    display = display.slice(GOAL_KICKOFF_PREFIX.length);
  } else if (display.startsWith(GOAL_CONTINUE_PREFIX)) {
    meta.goalMode = true;
    display = display.slice(GOAL_CONTINUE_PREFIX.length);
    const detailsStart = [
      display.indexOf("\nOpen criteria:"),
      display.indexOf("\nAll criteria appear covered;"),
    ].filter((index) => index >= 0);
    if (detailsStart.length > 0) {
      display = display.slice(0, Math.min(...detailsStart));
    }
  }
  return {
    content: display,
    meta,
  };
}

/* ---------- Connector runtime channel ----------
 * Mandate proposals and live-action chips render as standalone timeline items,
 * never folded into the thinking timeline (SPEC Consent §2 grouping note). They
 * are driven by dedicated state rather than the chat message store because they
 * are privileged-surface artifacts, not chat messages, and the proposal card
 * needs commit/adjust callbacks the generic MessageBubble does not carry. */
interface ProposalItem {
  kind: "proposal";
  timestamp: number;
  proposal: MandateProposal;
}
interface LiveActionItem {
  kind: "live_action";
  timestamp: number;
  action: LiveAction;
}
interface AgentAuditItem {
  kind: "agent_audit";
  timestamp: number;
  audit: AgentAudit;
}
interface TradePlanWidgetItem {
  kind: "trade_plan_widget";
  timestamp: number;
  widget: TradePlanWidget;
}
interface AutonomousProposalItem {
  kind: "autonomous_proposal";
  timestamp: number;
  proposal: AutonomousAgentProposal;
}
type LiveItem = ProposalItem | LiveActionItem | AgentAuditItem | TradePlanWidgetItem | AutonomousProposalItem;

function proposalSessionId(
  proposal: AutonomousAgentProposal,
  fallbackSessionId: string,
): string {
  return proposal.orchestrator_session_id || proposal.session_id || fallbackSessionId;
}

function upsertAutonomousProposalItems(
  items: LiveItem[],
  proposal: AutonomousAgentProposal,
  sessionId: string,
): LiveItem[] {
  if (!proposal.proposal_id || !["ready", "incomplete"].includes(proposal.status ?? "")) return items;
  const orchSid = proposalSessionId(proposal, sessionId);

  const marked = items.map((item) => {
    if (item.kind !== "autonomous_proposal") return item;
    const itemSid = proposalSessionId(item.proposal, sessionId);
    if (
      itemSid === orchSid &&
      item.proposal.proposal_id !== proposal.proposal_id &&
      !item.proposal.committed_agent_id
    ) {
      return {
        ...item,
        proposal: { ...item.proposal, superseded: true },
      };
    }
    return item;
  });

  const existingIdx = marked.findIndex(
    (item) =>
      item.kind === "autonomous_proposal" &&
      item.proposal.proposal_id === proposal.proposal_id,
  );
  if (existingIdx >= 0) {
    const next = [...marked];
    const existing = next[existingIdx];
    if (existing.kind === "autonomous_proposal") {
      next[existingIdx] = {
        ...existing,
        proposal: { ...proposal, superseded: proposal.superseded ?? false },
      };
    }
    return next;
  }

  return [...marked, { kind: "autonomous_proposal", timestamp: Date.now(), proposal }];
}

function normalizeBrokerScope(broker: string | null | undefined): string | null {
  const normalized = broker?.trim().toLowerCase();
  return normalized || null;
}

function isGlobalLiveHalt(halt: LiveHalted | null): boolean {
  return halt != null && normalizeBrokerScope(halt.broker) == null;
}

function haltScopeStillActive(halt: LiveHalted, status: LiveStatus): boolean {
  const broker = normalizeBrokerScope(halt.broker);
  if (!broker) return status.global_halted;
  return status.global_halted || status.brokers.some((item) => (
    normalizeBrokerScope(item.auth.broker) === broker && item.halted
  ));
}

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

function LiveActionChip({ action }: { action: LiveAction }) {
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
}

function AgentAuditChip({ audit }: { audit: AgentAudit }) {
  const kind = audit.kind?.replace(/_/g, " ") || "audit";
  return (
    <div className="flex gap-3">
      <AgentAvatar />
      <div className="flex-1 min-w-0">
        <div className="inline-flex max-w-full flex-wrap items-center gap-1.5 rounded-lg border border-violet-500/40 bg-violet-500/5 px-2.5 py-1 text-xs text-violet-700 dark:text-violet-300">
          <Activity className="h-3 w-3 shrink-0" />
          <span className="shrink-0 font-medium uppercase tracking-wide text-[10px]">Agent</span>
          <span className="shrink-0 font-medium">{kind}</span>
          {audit.outcome && (
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">· {audit.outcome}</span>
          )}
        </div>
      </div>
    </div>
  );
}

function isCriterionStatusMet(status: string): boolean {
  return !["", "pending", "open", "unsatisfied"].includes(status.toLowerCase());
}

function getGoalProgress(snapshot: GoalSnapshot | null): {
  met: number;
  total: number;
  label: string;
  metLabel: string;
  evidenceTotal: number;
} {
  const total = snapshot?.criteria.length ?? 0;
  const met = snapshot?.criteria.filter((item) => criterionCovered(snapshot, item)).length ?? 0;
  const evidenceTotal = snapshot?.evidence_count ?? 0;
  return {
    met,
    total,
    label: total > 0 ? `${met}/${total}` : "",
    metLabel: total > 0 ? `${met}/${total} met` : "",
    evidenceTotal,
  };
}

function statusLabel(status: string): string {
  return status.replace(/_/g, " ");
}

function isTerminalGoalStatus(status: string): boolean {
  return ["complete", "cancelled", "blocked", "superseded", "usage_limited"].includes(status);
}

function criterionIndexLabel(index: number): string {
  return String(index + 1);
}

function criterionEvidenceCount(snapshot: GoalSnapshot, criterionId: string): number {
  return snapshot.evidence.filter((item) => item.criterion_id === criterionId).length;
}

function criterionCovered(snapshot: GoalSnapshot, criterion: GoalSnapshot["criteria"][number]): boolean {
  return isCriterionStatusMet(criterion.status) || criterionEvidenceCount(snapshot, criterion.criterion_id) > 0;
}

function latestGoalEvidence(snapshot: GoalSnapshot) {
  return [...snapshot.evidence]
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
    .slice(0, 2);
}

function goalKickoffPrompt(objective: string): string {
  return [
    "Start working on this research goal now.",
    "Keep it research-only, use available tools when evidence is needed, add concrete evidence to the goal ledger, and keep going until the goal is complete, blocked, waiting for user input, or budget-limited.",
    "",
    `Goal: ${objective}`,
  ].join("\n");
}

function goalContinuePrompt(snapshot: GoalSnapshot): string {
  const openCriteria = snapshot.criteria
    .filter((item) => item.required && !criterionCovered(snapshot, item))
    .map((item) => `- ${item.text}`)
    .join("\n");
  return [
    "Continue the active research goal.",
    "Use real available tools as needed, add evidence to the goal ledger, and only stop when the goal is complete, blocked, waiting for user input, or budget-limited.",
    "",
    `Goal: ${snapshot.goal.objective}`,
    openCriteria ? `Open criteria:\n${openCriteria}` : "All criteria appear covered; audit the ledger and update the goal status if completion is justified.",
  ].join("\n");
}

function isObserveAgent(agent: AutonomousAgentInstance | null | undefined): boolean {
  return (agent?.mandate_config as { agent_mode?: string } | undefined)?.agent_mode === "observe";
}

function widgetApprovalState(
  widget: TradePlanWidget,
  agent: AutonomousAgentInstance | null | undefined,
): "pending" | "approved" | "informational" {
  if (!agent || isObserveAgent(agent)) return "informational";
  if (widget.meta?.revision_source === "watcher" || agent.plan_revision_source === "watcher") {
    return "informational";
  }
  const awaiting =
    agent.bootstrap_status === "awaiting_plan_approval" || Boolean(agent.plan_approval_required);
  const activeId = agent.active_trade_plan_widget_id;
  if (awaiting && activeId && widget.widget_id === activeId) {
    return "pending";
  }
  if (agent.approved_trade_plan_widget_id && widget.widget_id === agent.approved_trade_plan_widget_id) {
    return "approved";
  }
  if (awaiting) {
    return "pending";
  }
  return "informational";
}

/* ---------- Component ---------- */
interface AgentProps {
  embedded?: boolean;
  backToAutonomous?: () => void;
  onAutonomousAgentCommitted?: (agentId: string, sessionId: string) => void;
  autonomousAgent?: AutonomousAgentInstance | null;
  autonomousAgentId?: string | null;
  autonomousAgentLoadState?: "idle" | "loading" | "ready" | "error";
  onAutonomousAgentRefresh?: () => void;
  newsScenarioMode?: boolean;
  onNewsScenarioWidget?: (widget: TradePlanWidget) => void;
  boundPipelineAsOf?: string;
  pipelineStale?: boolean;
}

export function Agent({
  embedded: _embedded,
  backToAutonomous: _backToAutonomous,
  onAutonomousAgentCommitted,
  autonomousAgent,
  autonomousAgentId,
  autonomousAgentLoadState = "idle",
  onAutonomousAgentRefresh,
  newsScenarioMode = false,
  onNewsScenarioWidget,
  boundPipelineAsOf: _boundPipelineAsOf,
  pipelineStale = false,
}: AgentProps = {}) {
  const { t } = useTranslation();
  const [input, setInput] = useState("");
  const [searchParams, setSearchParams] = useSearchParams();
  const listRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<ComposerHandle>(null);
  const goalOpenRequestRef = useRef(0);
  const sseSessionRef = useRef<string | null>(null);
  const sseAgentRef = useRef<string | null>(null);
  const prevSseStatusRef = useRef<string>("disconnected");
  const genRef = useRef(0);
  const pendingGoalSessionRef = useRef<string | null>(null);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const lastEventRef = useRef(0);
  const sseTimeoutMsRef = useRef(90_000);

  /* tool_progress coalescing — keep latest payload per invocation, flush once per rAF. */
  const pendingProgressRef = useRef<Map<string, PendingToolProgress>>(new Map());
  const progressRafRef = useRef(0);
  const pendingTextRef = useRef("");
  const pendingReasoningRef = useRef<boolean | null>(null);
  const streamFlushTimerRef = useRef(0);
  const pendingSwarmEventsRef = useRef<Map<string, unknown[]>>(new Map());
  const swarmRafRef = useRef(0);
  const toolCallSeqRef = useRef(0);
  const replayAttemptSeenRef = useRef(false);
  const replayCheckTimerRef = useRef(0);
  const smoothScrollingRef = useRef(false);
  const smoothScrollTimerRef = useRef(0);
  const titleBeforeCompletionRef = useRef<string | null>(null);
  const completedAttemptIdsRef = useRef<Set<string>>(new Set());

  /* Composer-local state — the inline "+"/textarea/send bar in the return JSX
   * below is a self-contained composer (not the decomposed <Composer> component
   * in components/chat/Composer.tsx, which isn't wired into this page). */
  const [attachment, setAttachment] = useState<{ filename: string; filePath: string } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [showUploadMenu, setShowUploadMenu] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const isComposingRef = useRef(false);
  const lastCompositionEndRef = useRef(0);
  const uploadMenuRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [swarmPreset, setSwarmPreset] = useState<{ name: string; title: string } | null>(null);
  const [goalComposerActive, setGoalComposerActive] = useState(false);
  const [goalDetailsOpen, setGoalDetailsOpen] = useState(false);
  const [goalSnapshot, setGoalSnapshot] = useState<GoalSnapshot | null>(null);
  const [goalEditActive, setGoalEditActive] = useState(false);
  const [goalEditValue, setGoalEditValue] = useState("");

  /* Connector runtime channel state (SPEC Consent §1/§4/§5) */
  const [liveItems, setLiveItems] = useState<LiveItem[]>([]);
  const [visibleRowCount, setVisibleRowCount] = useState(TIMELINE_WINDOW_SIZE);
  const visibleRowsSessionRef = useRef<string | null>(null);
  const [llmSettings, setLlmSettings] = useState<LLMSettings | null>(null);
  const [committedMandates, setCommittedMandates] = useState<Record<string, MandateCommitted>>({});
  const [committedAutonomous, setCommittedAutonomous] = useState<Record<string, { agent_id?: string; name?: string }>>({});
  const [orchestratorMissingProposal, setOrchestratorMissingProposal] = useState(false);
  const [liveHalted, setLiveHalted] = useState<LiveHalted | null>(null);
  const [halting, setHalting] = useState(false);
  /* Bumped to force an immediate live-status re-poll on a live event
   * (commit / halt / resume / runner-affecting action) rather than waiting a tick. */
  const [liveStatusRefresh, setLiveStatusRefresh] = useState(0);
  /* Shared `GET /live/status` snapshot. Owned here (single poller) and passed down
   * to RunnerStatus, so the global kill switch can be shown whenever connector runtime
   * could be active out-of-band (CLI/another session), not only off in-session SSE
   * items (audit M2: always-available global halt — SPEC Consent §4). */
  const [liveStatus, setLiveStatus] = useState<LiveStatus | null>(null);
  const [reasoningActive, setReasoningActive] = useState(false);
  /* The status endpoint is not wired on every backend; a 404/501 hides the panel
   * and removes status from the kill-switch visibility condition. */
  const [liveStatusUnavailable, setLiveStatusUnavailable] = useState(false);
  const [tradingConnectors, setTradingConnectors] = useState<TradingConnectorsResponse | null>(null);
  const [connectorCheck, setConnectorCheck] = useState<{
    status: string;
    error?: string;
    broker?: string;
    broker_display?: string;
    analyze_mode?: boolean;
    host?: string;
    switch_url?: string;
    warning?: string;
    token_sync_warning?: string;
  } | null>(null);
  const clearedStaleOAuthHaltRef = useRef(false);

  /* Trade-stack research side panel (hub plan + TradingAgents debate) */
  const [researchTicker, setResearchTicker] = useState<string | null>(null);
  const [researchAssetType, setResearchAssetType] = useState("options");
  const [planArtifact, setPlanArtifact] = useState<HubPlanArtifact | null>(null);
  const [debateArtifact, setDebateArtifact] = useState<AgentDebateArtifact | null>(null);
  const [debateRunning, setDebateRunning] = useState(false);
  const [debateError, setDebateError] = useState<string | null>(null);
  const [sessionLoadError, setSessionLoadError] = useState<string | null>(null);

  const messages = useAgentStore(s => s.messages);
  const streamingText = useAgentStore(s => s.streamingText);
  const throttledStreamingText = useThrottledValue(streamingText);
  const status = useAgentStore(s => s.status);
  const sessionId = useAgentStore(s => s.sessionId);
  const activity = useAgentStore(s => s.activity);
  const toolCalls = useAgentStore(s => s.toolCalls);
  const reasoningTail = useAgentStore(s => s.reasoningTail);
  const swarmRuns = useAgentStore(s => s.swarmRuns);
  const sessionLoading = useAgentStore(s => s.sessionLoading);
  const previousStatusRef = useRef(status);

  const { connect, disconnect, onStatusChange } = useSSE();

  const urlSessionId = searchParams.get("session");
  const urlAgentId = searchParams.get("agent");
  const isOrchestratorView = urlAgentId === "orchestrator";

  const [runtimeIdentity, setRuntimeIdentity] = useState<RuntimeIdentity>({});
  const visibleRuntimeIdentity = runtimeIdentity.sessionId === urlSessionId
    ? runtimeIdentity
    : {};
  const isDraftCreateView = isOrchestratorView || autonomousAgent?.status === "draft";
  const isEmbeddedAutonomousView = Boolean(_embedded && urlAgentId && urlAgentId !== "orchestrator" && autonomousAgent?.status !== "draft");
  const shouldHydrateAutonomousProposal = useMemo(() => {
    const sid = sessionId || urlSessionId;
    if (!sid || !_embedded || !urlAgentId) return false;
    if (urlAgentId === "orchestrator") return true;
    if (autonomousAgent?.status === "draft") return true;
    // Draft record still loading after refresh — URL session id matches persisted proposal.
    if (autonomousAgentLoadState === "loading") return true;
    return false;
  }, [_embedded, autonomousAgent?.status, autonomousAgentLoadState, sessionId, urlAgentId, urlSessionId]);

  useEffect(() => {
    if (!isEmbeddedAutonomousView || !autonomousAgent) return;
    if (isObserveAgent(autonomousAgent)) return;
    const wid = autonomousAgent.active_trade_plan_widget_id;
    const awaiting =
      autonomousAgent.bootstrap_status === "awaiting_plan_approval" ||
      Boolean(autonomousAgent.plan_approval_required);
    if (!wid || !awaiting) return;
    let cancelled = false;
    void api
      .getTradeWidget(wid)
      .then((widget) => {
        if (cancelled || !widget?.widget_id) return;
        setLiveItems((items) => {
          if (
            items.some(
              (item) =>
                item.kind === "trade_plan_widget" &&
                item.widget.widget_id === widget.widget_id,
            )
          ) {
            return items;
          }
          return [
            ...items.filter(
              (item) =>
                !(
                  item.kind === "trade_plan_widget" &&
                  item.widget.underlying === widget.underlying
                ),
            ),
            {
              kind: "trade_plan_widget",
              timestamp: Date.now(),
              widget: { ...widget, meta: { ...widget.meta, superseded: false } },
            },
          ];
        });
      })
      .catch(() => null);
    return () => {
      cancelled = true;
    };
  }, [
    isEmbeddedAutonomousView,
    autonomousAgent?.active_trade_plan_widget_id,
    autonomousAgent?.bootstrap_status,
    autonomousAgent?.plan_approval_required,
  ]);

  useEffect(() => {
    if (!urlAgentId || urlAgentId === "orchestrator") return;
    if (autonomousAgent?.status === "draft") return;
    if (autonomousAgentLoadState === "loading") return;
    // Stop showing orchestrator proposal cards after promotion to an active agent.
    setLiveItems((items) =>
      items.filter(
        (item) =>
          item.kind !== "autonomous_proposal" ||
          committedAutonomous[item.proposal.proposal_id] != null,
      ),
    );
  }, [urlAgentId, autonomousAgent?.status, autonomousAgentLoadState, committedAutonomous]);

  useEffect(() => {
    const previous = previousStatusRef.current;
    previousStatusRef.current = status;
    if (previous === "streaming" && status !== "streaming") {
      requestAnimationFrame(() => {
        const selection = window.getSelection();
        if (selection && !selection.isCollapsed) return;
        const active = document.activeElement;
        if (
          active &&
          active !== document.body &&
          active !== inputRef.current &&
          !active.closest("[data-agent-composer]")
        ) {
          return;
        }
        inputRef.current?.focus({ preventScroll: true });
      });
    }
  }, [status]);

  /* Smart scroll — only auto-scroll when near bottom */
  const isNearBottom = useCallback(() => {
    const el = listRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 100;
  }, []);

  const rafRef = useRef(0);
  const scrollToBottom = useCallback(() => {
    if (smoothScrollingRef.current) return;
    if (!isNearBottom()) {
      setShowScrollBtn(true);
      return;
    }
    cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(() => {
      if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
    });
  }, [isNearBottom]);

  const forceScrollToBottom = useCallback((smooth = false) => {
    setShowScrollBtn(false);
    window.clearTimeout(smoothScrollTimerRef.current);
    smoothScrollingRef.current = smooth;
    requestAnimationFrame(() => {
      if (!listRef.current) return;
      if (smooth) {
        listRef.current.scrollTo({
          top: listRef.current.scrollHeight,
          behavior: "smooth",
        });
        smoothScrollTimerRef.current = window.setTimeout(() => {
          smoothScrollingRef.current = false;
          if (isNearBottom()) setShowScrollBtn(false);
        }, 500);
      } else {
        listRef.current.scrollTop = listRef.current.scrollHeight;
        smoothScrollingRef.current = false;
      }
    });
  }, [isNearBottom]);

  const flushPendingStreamUpdate = useCallback(() => {
    window.clearTimeout(streamFlushTimerRef.current);
    streamFlushTimerRef.current = 0;
    const pendingText = pendingTextRef.current;
    const pendingReasoning = pendingReasoningRef.current;
    pendingTextRef.current = "";
    pendingReasoningRef.current = null;
    if (pendingText) act().appendDelta(pendingText);
    if (pendingText || pendingReasoning !== null) scrollToBottom();
    return pendingText;
  }, [scrollToBottom]);

  const cancelPendingStreamFlush = useCallback(() => {
    window.clearTimeout(streamFlushTimerRef.current);
    streamFlushTimerRef.current = 0;
    pendingTextRef.current = "";
    pendingReasoningRef.current = null;
  }, []);

  const queueStreamUpdate = useCallback((text: string, reasoning: boolean) => {
    pendingTextRef.current += text;
    pendingReasoningRef.current = reasoning;
    if (streamFlushTimerRef.current) return;
    streamFlushTimerRef.current = window.setTimeout(
      flushPendingStreamUpdate,
      STREAM_FLUSH_INTERVAL_MS,
    );
  }, [flushPendingStreamUpdate]);

  const clearStreamingView = useCallback(() => {
    cancelPendingStreamFlush();
    act().clearStreaming();
  }, [cancelPendingStreamFlush]);

  const archiveActivity = useCallback((
    terminalState: "stopped" | "timeout" | "failed" | "done",
    endedAt = Date.now(),
  ): AgentActivity | null => {
    const store = act();
    store.setActivityState(terminalState, endedAt);
    const finished = act().activity;
    if (!finished) return null;
    const [activityMessage] = buildToolTimelineMessages(finished.steps, {
      activity: finished,
      idPrefix: "live_",
    });
    useAgentStore.setState((state) => ({
      messages: [
        ...state.messages.filter(
          (message) => message.meta?.activity?.attemptId !== finished.attemptId,
        ),
        activityMessage,
      ],
      activity: null,
      toolCalls: [],
    }));
    return finished;
  }, []);

  const persistPartialAnswer = useCallback((
    attempt: AgentActivity | null,
    content: string,
  ) => {
    if (!attempt || !content.trim()) return;
    act().addMessage({
      id: `partial_${attempt.attemptId}`,
      type: "answer",
      content,
      meta: { partialAttemptId: attempt.attemptId },
      timestamp: Date.now(),
    });
  }, []);

  const applyPersistedAutonomousProposal = useCallback(
    (proposal: AutonomousAgentProposal | null | undefined, sid: string): boolean => {
      if (!proposal?.proposal_id || !["ready", "incomplete"].includes(proposal.status ?? "")) {
        return false;
      }
      setOrchestratorMissingProposal(false);
      setLiveItems((items) => upsertAutonomousProposalItems(items, proposal, sid));
      return true;
    },
    [],
  );

  const hydrateAutonomousProposal = useCallback(
    async (
      sid: string,
      opts?: { retryMs?: number; scroll?: boolean; markMissingIfAbsent?: boolean },
    ): Promise<boolean> => {
      if (!sid) return false;
      const apply = (proposal: AutonomousAgentProposal | null | undefined) => {
        const ok = applyPersistedAutonomousProposal(proposal, sid);
        if (ok && opts?.scroll) scrollToBottom();
        return ok;
      };
      try {
        const latest = await api.getLatestAutonomousProposal(sid);
        if (apply(latest.proposal)) return true;
        if (opts?.retryMs) {
          await new Promise((resolve) => setTimeout(resolve, opts.retryMs));
          const retry = await api.getLatestAutonomousProposal(sid);
          if (apply(retry.proposal)) return true;
        }
      } catch {
        /* persisted proposal hydrate is best-effort */
      }
      if (opts?.markMissingIfAbsent) {
        setLiveItems((items) => {
          const hasCard = items.some(
            (item) =>
              item.kind === "autonomous_proposal" &&
              !item.proposal.committed_agent_id &&
              !item.proposal.superseded,
          );
          setOrchestratorMissingProposal(!hasCard);
          return items;
        });
      }
      return false;
    },
    [applyPersistedAutonomousProposal, scrollToBottom],
  );

  useEffect(() => {
    const sid = sessionId || urlSessionId;
    if (!shouldHydrateAutonomousProposal || !sid) return;
    void hydrateAutonomousProposal(sid, { scroll: true });
  }, [shouldHydrateAutonomousProposal, sessionId, urlSessionId, hydrateAutonomousProposal]);

  /* Track scroll position to show/hide scroll button */
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const onScroll = () => {
      if (smoothScrollingRef.current) return;
      if (isNearBottom()) setShowScrollBtn(false);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [isNearBottom]);

  useEffect(() => {
    onStatusChange((s) => {
      act().setSseStatus(s);
      if (s === "connected") {
        const connectedSid = sseSessionRef.current;
        window.clearTimeout(replayCheckTimerRef.current);
        replayCheckTimerRef.current = window.setTimeout(() => {
          const store = act();
          if (
            connectedSid &&
            sseSessionRef.current === connectedSid &&
            !replayAttemptSeenRef.current &&
            store.status !== "streaming"
          ) {
            store.clearStreamingSession(connectedSid);
          }
        }, 300);
      }
      if (s === "reconnecting" && prevSseStatusRef.current === "connected") toast.warning(t('agent.connectionLostReconnect'));
      else if (s === "connected" && prevSseStatusRef.current === "reconnecting") toast.success(t('agent.connectionRestored'));
      prevSseStatusRef.current = s;
    });
  }, [onStatusChange]);

  const handleDebateUpdate = useCallback(
    (debate: AgentDebateArtifact | null, running: boolean, error?: string | null) => {
      setDebateArtifact(debate);
      setDebateRunning(running);
      setDebateError(error ?? null);
    },
    [],
  );

  useEffect(() => {
    if (!debateRunning || !researchTicker) return;
    const poll = async () => {
      try {
        const res = await api.getAgentDebate(researchTicker);
        if (res.debate) {
          setDebateArtifact(res.debate);
          setDebateRunning(false);
          setDebateError(null);
        } else if (!res.running && res.status !== "running") {
          setDebateRunning(Boolean(res.running));
        }
      } catch {
        /* ignore transient poll errors */
      }
    };
    const id = window.setInterval(poll, 8000);
    void poll();
    return () => window.clearInterval(id);
  }, [debateRunning, researchTicker]);

  const doDisconnect = useCallback(() => {
    cancelPendingStreamFlush();
    window.clearTimeout(replayCheckTimerRef.current);
    disconnect();
    sseSessionRef.current = null;
  }, [cancelPendingStreamFlush, disconnect]);

  const loadGoalSnapshot = useCallback(async (sid?: string | null) => {
    const targetSession = sid || act().sessionId;
    goalOpenRequestRef.current = 0;
    if (!targetSession) {
      setGoalSnapshot(null);
      setGoalDetailsOpen(false);
      setGoalEditActive(false);
      return;
    }
    try {
      const snapshot = await api.getGoal(targetSession);
      if (act().sessionId !== targetSession) return;
      setGoalSnapshot(snapshot);
    } catch (error) {
      if (act().sessionId !== targetSession) return;
      if (error instanceof ApiError && error.status === 404) {
        setGoalSnapshot(null);
        setGoalDetailsOpen(false);
        setGoalEditActive(false);
      } else {
        toast.error(error instanceof Error ? error.message : t('agent.failedToLoadGoal'));
      }
    }
  }, []);

  const loadSessionMessages = useCallback(async (sid: string, gen: number) => {
    try {
      const msgs = await api.getSessionMessages(sid);
      if (genRef.current !== gen) return;
      const agentMsgs: StoredAgentMessage[] = [];
      let latestRuntimeIdentity: RuntimeIdentity | null = null;
      for (const m of msgs) {
        const meta = m.metadata as Record<string, unknown> | undefined;
        const runId = meta?.run_id as string | undefined;
        const metrics = meta?.metrics as Record<string, number> | undefined;
        const elapsedMs = typeof meta?.elapsed_ms === "number" ? meta.elapsed_ms : undefined;
        if (
          m.role === "assistant"
          && (
            typeof meta?.provider === "string"
            || typeof meta?.model === "string"
            || typeof meta?.reasoning_effort === "string"
          )
        ) {
          latestRuntimeIdentity = {
            sessionId: sid,
            provider: typeof meta?.provider === "string" ? meta.provider : undefined,
            model: typeof meta?.model === "string" ? meta.model : undefined,
            reasoningEffort: typeof meta?.reasoning_effort === "string"
              ? meta.reasoning_effort
              : undefined,
          };
        }
        const ts = new Date(m.created_at).getTime();
        const toolTimeline = m.role === "assistant"
          ? buildToolTimelineMessages(m.tool_trail ?? [], {
              fallbackTimestamp: ts,
              idPrefix: `${m.message_id}_`,
              attemptId: m.linked_attempt_id || `${m.message_id}_attempt`,
              state: meta?.status === "failed"
                ? "failed"
                : meta?.status === "cancelled"
                  ? "stopped"
                  : "done",
              endedAt: ts,
            })
          : [];
        agentMsgs.push(...toolTimeline);
        const lastToolTimestamp = toolTimeline.length > 0
          ? toolTimeline[toolTimeline.length - 1].timestamp
          : ts - 1;
        const assistantTs = Math.max(ts, lastToolTimestamp + 1);
        if (m.role === "user") {
          const displayPrompt = toDisplayPrompt(m.content);
          agentMsgs.push({
            id: m.message_id,
            type: "user",
            content: displayPrompt.content,
            meta: displayPrompt.meta,
            timestamp: ts,
          });
        } else if (runId) {
          // Show text answer first (if non-empty), then chart card
          if (m.content && m.content !== "Strategy execution completed.") {
            agentMsgs.push({ id: m.message_id + "_ans", type: "answer", content: m.content, elapsed_ms: elapsedMs, timestamp: assistantTs });
          }
          if (metrics && Object.keys(metrics).length > 0) {
            agentMsgs.push({ id: m.message_id, type: "run_complete", content: "", runId, metrics, timestamp: assistantTs + 1 });
          } else {
            // Fetch run data to check report-worthiness; show fallback card if fetch fails
            let fetchedMetrics: Record<string, number> | undefined;
            let fetchedCurve: Array<{ time: string; equity: number }> | undefined;
            let showCard = false;
            try {
              const runData = await api.getRun(runId);
              if (isReportWorthyRun(runData)) {
                fetchedMetrics = runData.metrics;
                fetchedCurve = runData.equity_curve?.map((e) => ({ time: e.time, equity: Number(e.equity) }));
                showCard = true;
              }
              // succeeded but not report-worthy (plain chat turn) → skip card
            } catch {
              // fetch failed (auth/404/network) → can't tell, show link as fallback
              showCard = true;
            }
            if (showCard) {
              agentMsgs.push({
                id: m.message_id,
                type: "run_complete",
                content: "",
                runId,
                metrics: fetchedMetrics,
                equityCurve: fetchedCurve,
                timestamp: assistantTs + 1,
              });
            }
          }
          if (metrics && Object.keys(metrics).length > 0) {
            agentMsgs.push({ id: m.message_id, type: "run_complete", content: "", runId, metrics, timestamp: ts + 1 });
          } else {
            // Fetch run data to check report-worthiness; show fallback card if fetch fails
            let fetchedMetrics: Record<string, number> | undefined;
            let fetchedCurve: Array<{ time: string; equity: number }> | undefined;
            let showCard = false;
            try {
              const runData = await api.getRun(runId);
              if (isReportWorthyRun(runData)) {
                fetchedMetrics = runData.metrics;
                fetchedCurve = runData.equity_curve?.map((e) => ({ time: e.time, equity: Number(e.equity) }));
                showCard = true;
              }
              // succeeded but not report-worthy (plain chat turn) → skip card
            } catch {
              // fetch failed (auth/404/network) → can't tell, show link as fallback
              showCard = true;
            }
            if (showCard) {
              agentMsgs.push({
                id: m.message_id,
                type: "run_complete",
                content: "",
                runId,
                metrics: fetchedMetrics,
                equityCurve: fetchedCurve,
                timestamp: ts + 1,
              });
            }
          }
        } else {
          agentMsgs.push({ id: m.message_id, type: "answer", content: m.content, elapsed_ms: elapsedMs, timestamp: assistantTs });
        }
      }
      if (genRef.current !== gen) return;
      act().loadHistory(agentMsgs);
      act().setSessionLoading(false);
      act().cacheSession(sid, agentMsgs);
      setRuntimeIdentity(latestRuntimeIdentity ?? {});
      setTimeout(() => forceScrollToBottom(), 50);
    } catch (error) {
      if (genRef.current !== gen) return;
      setRuntimeIdentity({});
      act().setSessionLoading(false);
      const message = error instanceof Error ? error.message : t('agent.failedToLoadSession');
      setSessionLoadError(message);
      toast.error(message);
    }
  }, [forceScrollToBottom, t]);

  const markBackgroundCompletion = useCallback((
    completedSessionId: string,
    attemptId?: string,
  ) => {
    if (attemptId && completedAttemptIdsRef.current.has(attemptId)) return;
    if (attemptId) completedAttemptIdsRef.current.add(attemptId);
    if (document.hidden) {
      titleBeforeCompletionRef.current ??= document.title;
      document.title = `● ${t("agent.doneMarker" as never)}`;
    }
    if (act().sessionId !== completedSessionId) {
      toast.success(t("agent.backgroundRunComplete" as never));
    }
  }, [t]);

  useEffect(() => {
    const restoreTitle = () => {
      if (document.hidden || titleBeforeCompletionRef.current === null) return;
      document.title = titleBeforeCompletionRef.current;
      titleBeforeCompletionRef.current = null;
    };
    document.addEventListener("visibilitychange", restoreTitle);
    return () => {
      document.removeEventListener("visibilitychange", restoreTitle);
      if (titleBeforeCompletionRef.current !== null) {
        document.title = titleBeforeCompletionRef.current;
        titleBeforeCompletionRef.current = null;
      }
    };
  }, []);

  const refreshSessionMessages = useCallback(async (sid: string) => {
    const gen = genRef.current + 1;
    genRef.current = gen;
    await loadSessionMessages(sid, gen);
  }, [loadSessionMessages]);

  const syncCompletedAttempt = useCallback(async (sid: string, attemptId?: string) => {
    if (!attemptId) return false;
    for (let i = 0; i < 3; i += 1) {
      try {
        const storedMessages = await api.getSessionMessages(sid);
        const completed = storedMessages.some(
          (message) => message.role === "assistant" && message.linked_attempt_id === attemptId,
          );
        if (completed) {
          act().clearStreamingSession(sid);
          markBackgroundCompletion(sid, attemptId);
          if (act().sessionId !== sid) return true;
          clearStreamingView();
          act().setStatus("idle");
          useAgentStore.setState({ toolCalls: [], activity: null });
          await refreshSessionMessages(sid);
          return true;
        }
      } catch {
        return false;
      }
      await new Promise<void>((resolve) => window.setTimeout(resolve, 800));
    }
    return false;
  }, [clearStreamingView, markBackgroundCompletion, refreshSessionMessages]);

  const setupSSE = useCallback((sid: string) => {
    if (sseSessionRef.current === sid) return;
    cancelPendingStreamFlush();
    window.clearTimeout(replayCheckTimerRef.current);
    replayAttemptSeenRef.current = false;
    disconnect();
    sseSessionRef.current = sid;
    sseAgentRef.current = urlAgentId;

    const touch = () => { lastEventRef.current = Date.now(); };
    const identifyActivity = (
      data: Record<string, unknown>,
      state: "thinking" | "working" | "responding",
    ): boolean => {
      const store = act();
      const attemptId = String(data.attempt_id || "");
      if (attemptId && store.messages.some((message) => (
        message.meta?.activity?.attemptId === attemptId
        && ["stopped", "timeout"].includes(message.meta.activity.state)
      ))) {
        return false;
      }
      const current = store.activity;
      if (!current) {
        store.startActivity(attemptId || `pending-${Date.now()}`);
      } else if (
        attemptId &&
        current.attemptId !== attemptId &&
        current.attemptId.startsWith("pending-")
      ) {
        store.setActivityAttemptId(attemptId);
      } else if (attemptId && current.attemptId !== attemptId) {
        store.startActivity(attemptId);
      }
      act().setActivityState(state);
      return true;
    };

    connect(api.sseUrl(sid, { replay: "active" }), {
      text_delta: (d) => {
        touch();
        replayAttemptSeenRef.current = true;
        if (!identifyActivity(d, "responding")) return;
        if (act().status !== "streaming") act().setStatus("streaming");
        queueStreamUpdate(String(d.delta || ""), false);
      },
      reasoning_delta: (d) => {
        touch();
        replayAttemptSeenRef.current = true;
        if (!identifyActivity(d, "thinking")) return;
        // The backend sends a bounded rolling tail — replace, never append.
        act().setReasoningTail(String(d.tail ?? ""));
        queueStreamUpdate("", true);
        if (act().status !== "streaming") act().setStatus("streaming");
      },
      stream_reset: (d) => {
        touch();
        if (!identifyActivity(d, "thinking")) return;
        clearStreamingView();
        if (act().status !== "streaming") act().setStatus("streaming");
        scrollToBottom();
      },
      thinking_done: () => { touch(); /* don't flush — keep streaming text visible */ },

      tool_call: (d) => {
        touch();
        replayAttemptSeenRef.current = true;
        if (!identifyActivity(d, "working")) return;
        queueStreamUpdate("", false);
        const toolName = String(d.tool || "");
        const callId = eventCallId(d);
        toolCallSeqRef.current += 1;
        // Only update toolCalls tracker (no message creation during streaming)
        act().addToolCall({
          id: callId ?? `${toolName}#${toolCallSeqRef.current}`, tool: toolName,
          arguments: (d.arguments as Record<string, string>) ?? {},
          status: "running", timestamp: Date.now(),
        });
        scrollToBottom();
      },

      tool_result: (d) => {
        touch();
        const toolName = String(d.tool || "");
        const callId = eventCallId(d);
        // Drop any in-flight coalesced progress for this invocation.
        pendingProgressRef.current.delete(toolProgressKey(callId, toolName));
        // Only update tracker (no message creation during streaming)
        act().updateRunningToolCall(callId, toolName, {
          status: d.status === "ok" ? "ok" : "error",
          preview: String(d.preview || ""),
          elapsed_ms: Number(d.elapsed_ms || 0),
          elapsed_s: undefined,
          progress: undefined,
        });
        if (toolName === "run_swarm") {
          const fallback = buildSwarmStatusFromToolResultPreview(String(d.preview || ""));
          if (fallback && !act().swarmRuns[fallback.runId]) {
            act().upsertSwarmStatus(fallback);
          }
        }
      },

      tool_heartbeat: (d) => {
        touch();
        replayAttemptSeenRef.current = true;
        if (!identifyActivity(d, "working")) return;
        // Keep streaming state alive during long-running tools (swarm, backtest)
        if (act().status !== "streaming") act().setStatus("streaming");
        const toolName = String(d.tool || "");
        if (!toolName) return;
        act().updateRunningToolCall(eventCallId(d), toolName, {
          elapsed_s: Number(d.elapsed_s || 0),
        });
      },

      tool_progress: (d) => {
        touch();
        replayAttemptSeenRef.current = true;
        const toolName = String(d.tool || "");
        if (!toolName) return;
        const callId = eventCallId(d);
        const payload: NonNullable<ToolCallEntry["progress"]> = {};
        if (typeof d.stage === "string" && d.stage) payload.stage = d.stage;
        if (typeof d.message === "string" && d.message) payload.message = d.message;
        if (typeof d.current === "number") payload.current = d.current;
        if (typeof d.total === "number") payload.total = d.total;
        // Coalesce: keep latest payload per invocation, flush once per animation frame.
        pendingProgressRef.current.set(
          toolProgressKey(callId, toolName),
          { callId, tool: toolName, progress: payload },
        );
        if (progressRafRef.current) return;
        progressRafRef.current = requestAnimationFrame(() => {
          progressRafRef.current = 0;
          const pending = pendingProgressRef.current;
          if (pending.size === 0) return;
          const store = act();
          for (const { callId: pendingCallId, tool, progress } of pending.values()) {
            store.updateRunningToolCall(pendingCallId, tool, { progress });
          }
          pending.clear();
        });
      },

      compact: () => { touch(); },

      "message.received": (d) => {
        touch();
        if (String(d.role || "") !== "user") return;
        const messageId = String(d.message_id || "");
        const requestText = String(d.content || "");
        if (!messageId || !requestText || act().messages.some((msg) => msg.id === messageId)) return;
        const now = Date.now();
        const optimisticDuplicate = act().messages.some((msg) => (
          msg.type === "user" &&
          msg.meta?.requestText === requestText &&
          now - msg.timestamp < 10_000
        ));
        if (optimisticDuplicate) return;
        const displayPrompt = toDisplayPrompt(requestText);
        act().addMessage({
          id: messageId,
          type: "user",
          content: displayPrompt.content,
          meta: displayPrompt.meta,
          timestamp: now,
        });
        scrollToBottom();
      },

      "attempt.created": (d) => {
        touch();
        replayAttemptSeenRef.current = true;
        if (!identifyActivity(d, "thinking")) return;
        // Backend has created a new attempt — ensure streaming state is active
        // even if we connected mid-stream (SSE replay / page reload).
        if (act().status !== "streaming") act().setStatus("streaming");
      },

      "attempt.started": (d) => {
        touch();
        replayAttemptSeenRef.current = true;
        if (!identifyActivity(d, "thinking")) return;
        // Backend has begun executing the attempt. Re-affirm streaming state
        // so the UI shows a working indicator for reconnects and fresh loads.
        if (act().status !== "streaming") act().setStatus("streaming");
      },

      "attempt.completed": async (d) => {
        touch();
        setReasoningActive(false);
        const attemptId = String(d.attempt_id || "");
        markBackgroundCompletion(sid, attemptId);
        if (act().sessionId !== sid) {
          act().clearStreamingSession(sid);
          return;
        }
        if (attemptId && act().messages.some(
          (message) => (
            message.meta?.activity?.attemptId === attemptId
            && message.meta.activity.state === "done"
          ),
        )) {
          clearStreamingView();
          act().setStatus("idle");
          return;
        }
        const streamedAnswer = act().streamingText + pendingTextRef.current;
        flushPendingStreamUpdate();
        if (!act().activity) {
          act().startActivity(attemptId || `completed-${Date.now()}`);
        } else if (attemptId && act().activity?.attemptId !== attemptId) {
          act().setActivityAttemptId(attemptId);
        }
        const s = act();
        const completedTools = s.activity?.steps ?? s.toolCalls;
        const completedActivity = archiveActivity("done");
        const completedAttemptId = completedActivity?.attemptId || attemptId;
        useAgentStore.setState((state) => ({
          messages: state.messages.filter(
            (message) => message.meta?.partialAttemptId !== completedAttemptId,
          ),
        }));

        // Clear streaming text after freezing the durable activity summary.
        s.clearStreaming();

        // Add final answer
        const runDir = String(d.run_dir || "");
        const runId = runDir ? runDir.split(/[/\\]/).pop() : undefined;
        const summary = String(d.summary || "");
        const finalAnswer = summary.trim() ? summary : streamedAnswer;
        if (finalAnswer) {
          s.addMessage({
            id: "",
            type: "answer",
            content: finalAnswer,
            elapsed_ms: Number(d.elapsed_ms || 0) || undefined,
            timestamp: Date.now(),
          });
        }
        if (d.provider || d.model) {
          setRuntimeIdentity({
            sessionId: sid,
            provider: d.provider ? String(d.provider) : undefined,
            model: d.model ? String(d.model) : undefined,
            reasoningEffort: typeof d.reasoning_effort === "string"
              ? d.reasoning_effort
              : undefined,
          });
        }

        // First completed exchange → ask the backend for a Codex-style
        // summary title, then nudge the sidebar to re-list sessions.
        if (act().messages.filter((message) => message.type === "user").length === 1) {
          api.autoTitleSession(sid)
            .then(() => window.dispatchEvent(new Event("vibe:sessions-refresh")))
            .catch(() => {});
        }

        // Detect Shadow Account id if render_shadow_report fired successfully this turn
        const shadowCall = completedTools.find(
          (tc) => tc.tool === "render_shadow_report" && (tc.status || "ok") === "ok",
        );
        const shadowMatch = shadowCall?.preview?.match(/"shadow_id"\s*:\s*"(shadow_[A-Za-z0-9_]+)"/);
        const shadowId = shadowMatch?.[1];

        // The transcript is already complete. Drop transient state before the
        // optional report fetch so answer/tool progress never overlap.
        s.setStatus("idle");
        scrollToBottom();

        // Show RunCompleteCard when the turn produced backtest metrics or a shadow report
        if (runId) {
          let runMetrics: Record<string, number> | undefined;
          let runCurve: Array<{ time: string; equity: number }> | undefined;
          let showCard = false;
          try {
            const runData = await api.getRun(runId);
            if (isReportWorthyRun(runData)) {
              runMetrics = runData.metrics;
              runCurve = runData.equity_curve?.map(e => ({ time: e.time, equity: Number(e.equity) }));
              showCard = true;
            }
          } catch {
            showCard = true; // fetch failed → show link as fallback
          }
          if (showCard || shadowId) {
            s.addMessage({
              id: "", type: "run_complete", content: "", runId,
              metrics: showCard ? runMetrics : undefined,
              equityCurve: showCard ? runCurve : undefined,
              shadowId,
              timestamp: Date.now(),
            });
          }
        } else if (shadowId) {
          s.addMessage({ id: "", type: "run_complete", content: "", shadowId, timestamp: Date.now() });
        }

        if (isDraftCreateView && sid) {
          void hydrateAutonomousProposal(sid, {
            retryMs: 2000,
            scroll: true,
            markMissingIfAbsent: true,
          });
        }

        scrollToBottom();
      },

      "attempt.failed": (d) => {
        touch();
        setReasoningActive(false);
        act().clearStreaming();
        act().addMessage({ id: "", type: "error", content: String(d.error || "Execution failed"), timestamp: Date.now() });
        act().setStatus("idle");
        // Clear stale toolCalls so the next turn's running indicator doesn't
        // briefly show the previous turn's progress before fresh events land.
        useAgentStore.setState({ toolCalls: [] });
        scrollToBottom();
      },

      "autonomous_agent.proposal": (d) => {
        touch();
        const proposal = d as unknown as AutonomousAgentProposal;
        if (!proposal.proposal_id || !["ready", "incomplete"].includes(proposal.status ?? "")) return;
        setOrchestratorMissingProposal(false);
        setLiveItems((items) => upsertAutonomousProposalItems(items, proposal, sid));
        scrollToBottom();
      },

      "autonomous_agent.committed": (d) => {
        touch();
        const payload = d as { agent_id?: string; vibe_session_id?: string; name?: string };
        if (!payload.agent_id || !payload.vibe_session_id) return;
        window.dispatchEvent(new Event("autonomous-agents-refresh"));
        void refreshSessionMessages(sid);
        onAutonomousAgentCommitted?.(payload.agent_id, payload.vibe_session_id);
      },

      "autonomous_agent.intent_updated": (d) => {
        touch();
        const payload = d as { summary?: string };
        const summary = String(payload.summary || "").replace(/\*\*/g, "");
        if (summary) {
          toast.message(summary);
        }
      },

      "session.promoted": (d) => {
        touch();
        const payload = d as { agent_id?: string; session_id?: string };
        if (payload.agent_id && payload.session_id) {
          void refreshSessionMessages(sid);
          onAutonomousAgentCommitted?.(payload.agent_id, payload.session_id);
        }
      },

      "trade_plan.widget": (d) => {
        touch();
        const widget = d as unknown as TradePlanWidget;
        if (!widget.widget_id || widget.type !== "trade_plan.widget") return;
        if (widget.widget_kind === "news_event_scenario") {
          onNewsScenarioWidget?.(widget);
        }
        setResearchTicker(widget.underlying);
        setResearchAssetType(
          widget.asset_type === "stock"
            ? "stock"
            : widget.asset_type === "index"
              ? "index"
              : "options",
        );
        const isAutonomousSession =
          Boolean(_embedded && urlAgentId && urlAgentId !== "orchestrator");
        setLiveItems((items) => {
          if (
            items.some(
              (item) =>
                item.kind === "trade_plan_widget" &&
                item.widget.widget_id === widget.widget_id,
            )
          ) {
            return items;
          }
          if (isAutonomousSession) {
            const withoutSameUnderlying = items.filter(
              (item) =>
                !(
                  item.kind === "trade_plan_widget" &&
                  item.widget.underlying === widget.underlying
                ),
            );
            return [
              ...withoutSameUnderlying,
              {
                kind: "trade_plan_widget",
                timestamp: Date.now(),
                widget: {
                  ...widget,
                  meta: {
                    ...widget.meta,
                    superseded: false,
                    revision_source:
                      widget.meta?.revision_source ||
                      (autonomousAgent?.plan_revision_source === "watcher"
                        ? "watcher"
                        : undefined),
                  },
                },
              },
            ];
          }
          return [
            ...items,
            { kind: "trade_plan_widget", timestamp: Date.now(), widget },
          ];
        });
        scrollToBottom();
      },

      "autonomous_agent.plan_ready": () => {
        touch();
        window.dispatchEvent(new Event("autonomous-agents-refresh"));
      },

      "autonomous_agent.plan_approved": () => {
        touch();
        window.dispatchEvent(new Event("autonomous-agents-refresh"));
      },

      "plan.stale": (d) => {
        touch();
        const ticker = String(d.ticker || "");
        const status = String(d.status || "stale");
        const reasons = Array.isArray(d.reasons) ? (d.reasons as string[]) : [];
        if (ticker) setResearchTicker(ticker);
        const reasonText = reasons.length ? reasons.join(", ") : "market moved";
        toast.warning(`Plan for ${ticker} may be outdated (${status}: ${reasonText})`);
        setPlanArtifact((prev) => {
          const prevTicker = (prev?.ticker || prev?.underlying || "").toUpperCase();
          if (!prev || prevTicker !== ticker.toUpperCase()) return prev;
          return {
            ...prev,
            staleness: {
              status,
              reasons,
              suggested_action: String(d.suggested_action || "refresh"),
            },
          };
        });
      },

      "research.artifact": (d) => {
        touch();
        const ticker = String(d.ticker || "");
        const artifact = d.artifact as HubPlanArtifact | undefined;
        if (ticker) setResearchTicker(ticker);
        if (d.asset_type) setResearchAssetType(String(d.asset_type));
        if (artifact) setPlanArtifact(artifact);
      },

      "research.debate": (d) => {
        touch();
        const ticker = String(d.ticker || "");
        if (ticker) setResearchTicker(ticker);
        const status = String(d.status || "");
        if (status === "started" || status === "running") {
          setDebateRunning(true);
          setDebateError(null);
          return;
        }
        if (status === "error") {
          setDebateRunning(false);
          setDebateError(String(d.message || "Agent debate failed"));
          return;
        }
        if (status === "ready" && d.debate) {
          setDebateRunning(false);
          setDebateError(null);
          setDebateArtifact(d.debate as AgentDebateArtifact);
        }
      },

      "provenance.source": (d) => {
        touch();
        const source = d.source as ProvenanceSource | undefined;
        if (source?.ref_id) {
          const store = useProvenanceStore.getState();
          const wasEmpty = store.sources.length === 0;
          store.upsertSource(source);
          if (wasEmpty) {
            store.setDrawerOpen(true);
            store.setActiveSection("sources");
          }
        }
      },

      "mandate.committed": (d) => {
        touch();
        const committed = d as unknown as MandateCommitted;
        if (!committed.proposal_id) return;
        setCommittedMandates((prev) => ({ ...prev, [committed.proposal_id as string]: committed }));
        // A fresh mandate may bring up the runner; refresh the runtime panel now.
        setLiveStatusRefresh((n) => n + 1);
        scrollToBottom();
      },

      "live.halted": (d) => {
        touch();
        const halted = d as unknown as LiveHalted;
        // Preemptive kill switch: the server has cancelled resting orders and may have
        // flattened positions (SPEC §7.5 #6). Reflect the halted state across surfaces;
        // the RunnerStatus panel re-polls so its per-broker rows show "halted".
        setLiveHalted(halted);
        setLiveStatusRefresh((n) => n + 1);
        toast.warning(t('agent.connectorHalted'));
      },

      "live.resumed": (d) => {
        touch();
        // Kill switch cleared via a privileged surface action (SPEC Consent §4);
        // clear the halted banner and re-poll runtime status.
        void d;
        setLiveHalted(null);
        setLiveStatusRefresh((n) => n + 1);
        toast.success(t('agent.connectionRestored'));
      },

      "live.action": (d) => {
        touch();
        const action = d as unknown as LiveAction;
        if (!action.kind) return;
        setLiveItems((items) => [...items, { kind: "live_action", timestamp: Date.now(), action }]);
        if (action.kind === "halt_tripped") setLiveHalted({ broker: action.broker, reason: action.intent_normalized });
        if (action.kind === "halt_cleared") setLiveHalted(null);
        // Mandate-affecting / runner-affecting actions should refresh the runtime panel.
        if (["mandate_committed", "halt_tripped", "halt_cleared"].includes(action.kind)) {
          setLiveStatusRefresh((n) => n + 1);
        }
        scrollToBottom();
      },

      "agent.action": (d) => {
        touch();
        const audit = d as unknown as AgentAudit;
        if (!audit.kind) return;
        setLiveItems((items) => [...items, { kind: "agent_audit", timestamp: Date.now(), audit }]);
        scrollToBottom();
      },

      // A user stop is its own terminal event now, so it no longer arrives as
      // attempt.failed and no longer needs the pre-marked "stopped" workaround
      // to avoid showing an error bubble.
      "attempt.cancelled": (d) => {
        touch();
        const attemptId = String(d.attempt_id || "");
        if (act().sessionId !== sid) {
          act().clearStreamingSession(sid);
          return;
        }
        const archived = act().messages.find(
          (message) => message.meta?.activity?.attemptId === attemptId,
        )?.meta?.activity;
        if (archived && archived.state !== "stopped") {
          useAgentStore.setState((state) => ({
            messages: state.messages.map((message) => (
              message.meta?.activity?.attemptId === attemptId
                ? {
                    ...message,
                    meta: {
                      ...message.meta,
                      activity: { ...archived, state: "stopped", endedAt: Date.now() },
                    },
                  }
                : message
            )),
          }));
        } else if (!archived) {
          if (!act().activity) act().startActivity(attemptId || `cancelled-${Date.now()}`);
          archiveActivity("stopped");
        }
        clearStreamingView();
        act().setStatus("idle");
        scrollToBottom();
      },

      "goal.created": () => {
        touch();
        loadGoalSnapshot(sid);
      },

      "swarm.started": (d) => {
        touch();
        replayAttemptSeenRef.current = true;
        const status = buildSwarmStatusFromStarted(d);
        if (!status) return;
        act().upsertSwarmStatus(status);
        scrollToBottom();
      },

      "swarm.event": (d) => {
        touch();
        replayAttemptSeenRef.current = true;
        if (act().status !== "streaming") act().setStatus("streaming");
        const runId = String(d.run_id || "");
        const event = d.event;
        if (!runId || !event) return;
        const pending = pendingSwarmEventsRef.current.get(runId) ?? [];
        pending.push(event);
        pendingSwarmEventsRef.current.set(runId, pending);
        if (swarmRafRef.current) return;
        swarmRafRef.current = requestAnimationFrame(() => {
          swarmRafRef.current = 0;
          const batches = pendingSwarmEventsRef.current;
          pendingSwarmEventsRef.current = new Map();
          const store = act();
          for (const [pendingRunId, events] of batches) {
            store.updateSwarmStatus(pendingRunId, (current) => (
              events.reduce<SwarmRunStatus>(
                (next, pendingEvent) => applySwarmEvent(next, pendingEvent),
                current,
              )
            ));
          }
          scrollToBottom();
        });
      },

      "goal.evidence": () => {
        touch();
        loadGoalSnapshot(sid);
      },

      "goal.updated": (d) => {
        touch();
        const snapshot = d.snapshot as GoalSnapshot | undefined;
        const goal = (d.goal as GoalSnapshot["goal"] | undefined) ?? snapshot?.goal;
        if (goal && isTerminalGoalStatus(goal.status)) {
          setGoalSnapshot(null);
          return;
        }
        if (snapshot) {
          setGoalSnapshot(snapshot);
          return;
        }
        loadGoalSnapshot(sid);
      },

      "mandate.proposal": (d) => {
        touch();
        const proposal = d as unknown as MandateProposal;
        if (!proposal.proposal_id || !Array.isArray(proposal.profiles)) return;
        setLiveItems((items) => [
          ...items,
          { kind: "proposal", timestamp: Date.now(), proposal, committed: null },
        ]);
        scrollToBottom();
      },

      heartbeat: () => {},
      reconnect: (d) => { act().setSseStatus("reconnecting", Number(d.attempt ?? 0)); },
    });
  }, [connect, disconnect, hydrateAutonomousProposal, isDraftCreateView, loadGoalSnapshot, newsScenarioMode, onAutonomousAgentCommitted, onNewsScenarioWidget, refreshSessionMessages, scrollToBottom, urlAgentId]);

  useEffect(() => {
    const { sessionId: curSid, messages: curMsgs, cacheSession, reset, getCachedSession, switchSession } = act();

    if (urlSessionId && urlSessionId !== curSid) {
      const gen = genRef.current + 1;
      genRef.current = gen;
      doDisconnect();
      // Live-channel timeline items are per-session; clear on switch.
      setLiveItems([]);
      setCommittedMandates({});
      setLiveHalted(null);
      setLiveStatusRefresh((n) => n + 1);
      useProvenanceStore.getState().reset();
      if (curSid && curMsgs.length > 0) cacheSession(curSid, curMsgs);

      // Atomic switch: cache hit = instant, cache miss = show loading skeleton
      const cached = getCachedSession(urlSessionId);
      switchSession(urlSessionId, cached);
      if (cached) {
        setTimeout(() => forceScrollToBottom(), 50);
      }
      // Cached rows provide an instant shell; REST remains authoritative for a
      // turn that completed while this session was off-screen.
      loadSessionMessages(urlSessionId, gen);
      setupSSE(urlSessionId);
    } else if (urlSessionId && urlSessionId === curSid && sseSessionRef.current !== urlSessionId) {
      // #229: returning to the SAME session after the page was unmounted (user
      // navigated away and back). The store kept our messages, but the unmount
      // cleanup tore down the SSE stream, so a running attempt stopped updating
      // and the UI looked frozen until the safety timeout fired. Re-hydrate like
      // a reload: reset the transient streaming view first (so replay=active
      // rebuilds the in-flight turn from the backend ring buffer instead of
      // duplicating deltas onto the preserved text), refresh committed history
      // (covers an attempt that finished while we were away), then re-subscribe.
      const gen = genRef.current + 1;
      genRef.current = gen;
      setRuntimeIdentity({});
      const seed = curMsgs.length > 0 ? curMsgs : getCachedSession(urlSessionId);
      switchSession(urlSessionId, seed);
      loadSessionMessages(urlSessionId, gen);
      setupSSE(urlSessionId);
    } else if (!urlSessionId && curSid) {
      genRef.current += 1;
      doDisconnect();
      setLiveItems([]);
      setCommittedMandates({});
      setLiveHalted(null);
      setLiveStatusRefresh((n) => n + 1);
      if (curSid && curMsgs.length > 0) cacheSession(curSid, curMsgs);
      reset();
    }
  }, [urlSessionId, urlAgentId, doDisconnect, loadSessionMessages, setupSSE, forceScrollToBottom]);

  /* Single shared poller for `GET /live/status`. RunnerStatus consumes this snapshot
   * as a prop rather than polling independently, and the global kill switch reads it
   * to stay available whenever connector runtime activity could be active out-of-band. */
  const refreshLiveStatus = useCallback(async () => {
    try {
      const next = await api.getLiveStatus();
      setLiveStatus(next);
      setLiveHalted((current) => (
        current && !haltScopeStillActive(current, next) ? null : current
      ));
      setLiveStatusUnavailable(false);
    } catch (error) {
      if (error instanceof ApiError && (error.status === 404 || error.status === 501)) {
        setLiveStatus(null);
        setLiveStatusUnavailable(true);
      }
    }
  }, []);

  const refreshTradingConnectors = useCallback(async () => {
    try {
      const payload = await api.getTradingConnectors();
      setTradingConnectors(payload);
      const selected = payload.profiles.find((p) => p.selected);
      if (!selected) {
        setConnectorCheck(null);
        return payload;
      }
      const check = await api.checkTradingConnector(selected.id);
      setConnectorCheck({
        status: check.status,
        error: typeof check.error === "string" ? check.error : undefined,
        broker: typeof check.broker === "string" ? check.broker : undefined,
        broker_display: typeof check.broker_display === "string" ? check.broker_display : undefined,
        analyze_mode: typeof check.analyze_mode === "boolean" ? check.analyze_mode : undefined,
        host: typeof check.host === "string" ? check.host : undefined,
        switch_url: typeof check.switch_url === "string" ? check.switch_url : undefined,
        warning: typeof check.warning === "string" ? check.warning : undefined,
        token_sync_warning: typeof check.token_sync_warning === "string" ? check.token_sync_warning : undefined,
      });
      return payload;
    } catch (error) {
      console.warn("Failed to load trading connectors", error);
      return null;
    }
  }, []);

  const refreshConnectorRuntime = useCallback(async () => {
    await Promise.all([refreshLiveStatus(), refreshTradingConnectors()]);
  }, [refreshLiveStatus, refreshTradingConnectors]);

  useEffect(() => {
    refreshConnectorRuntime();
    const timer = setInterval(refreshConnectorRuntime, LIVE_STATUS_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [refreshConnectorRuntime]);

  /* Clear a stale global OAuth halt when the user runs an SDK connector (e.g. OpenAlgo paper). */
  useEffect(() => {
    if (clearedStaleOAuthHaltRef.current) return;
    const selected = tradingConnectors?.profiles.find((p) => p.selected);
    if (!selected || selected.transport !== "broker_sdk") return;
    if (connectorCheck?.status !== "ok") return;
    if (!liveStatus?.global_halted) return;
    const oauthEngaged = liveStatus.brokers.some((b) => (
      b.auth.oauth_token_present || b.runner?.alive || b.mandate != null
    ));
    if (oauthEngaged) return;
    clearedStaleOAuthHaltRef.current = true;
    api.resumeLive().then(() => refreshConnectorRuntime()).catch(() => {
      clearedStaleOAuthHaltRef.current = false;
    });
  }, [tradingConnectors, connectorCheck, liveStatus, refreshConnectorRuntime]);

  // Force an immediate re-poll when a live event bumps refreshKey (commit/halt/resume).
  useEffect(() => {
    if (liveStatusRefresh > 0) refreshConnectorRuntime();
  }, [liveStatusRefresh, refreshConnectorRuntime]);

  useEffect(() => {
    if (!sessionId) {
      setGoalSnapshot(null);
      setGoalDetailsOpen(false);
      return;
    }
    if (pendingGoalSessionRef.current === sessionId) {
      pendingGoalSessionRef.current = null;
      return;
    }
    loadGoalSnapshot(sessionId);
  }, [sessionId, loadGoalSnapshot]);

  useEffect(() => {
    api.getLLMSettings().then((s) => {
      sseTimeoutMsRef.current = s.sse_timeout_seconds * 1000;
      setLlmSettings(s);
    }).catch(() => {});
  }, []);

  /* Safety timeout: if streaming but no SSE event for sseTimeoutMsRef.current ms, reset to idle */
  useEffect(() => {
    if (status !== "streaming") return;
    // Arm the clock at the start of every streaming turn. Without this, a turn
    // whose very first event never arrives (e.g. the LLM provider hangs before
    // emitting a single token) left lastEventRef at its 0 / stale value, so the
    // guard below short-circuited and the activity line stayed live forever.
    // forever. touch() refreshes this on every real event; the no-op heartbeat
    // deliberately does not, so a connection that only keep-alives still trips.
    lastEventRef.current = Date.now();
    const timer = setInterval(() => {
      if (lastEventRef.current && Date.now() - lastEventRef.current > sseTimeoutMsRef.current && act().status === "streaming") {
        flushPendingStreamUpdate();
        const partialAnswer = act().streamingText;
        const timedOutActivity = archiveActivity("timeout");
        act().clearStreaming();
        persistPartialAnswer(timedOutActivity, partialAnswer);
        act().setStatus("idle");
        toast.warning(t('agent.executionTimedOut'));
      }
    }, 10_000);
    return () => clearInterval(timer);
  }, [archiveActivity, flushPendingStreamUpdate, persistPartialAnswer, status, t]);

  const ensureGoalSession = useCallback(async (title: string): Promise<string> => {
    let sid = act().sessionId;
    if (sid) return sid;
    const session = await api.createSession(title.slice(0, 50));
    sid = session.session_id;
    pendingGoalSessionRef.current = sid;
    act().setSessionId(sid);
    setSearchParams({ session: sid }, { replace: true });
    setupSSE(sid);
    return sid;
  }, [setSearchParams, setupSSE]);

  const runPrompt = useCallback(async (prompt: string) => {
    if (!prompt.trim() || status === "streaming") return;
    clearStreamingView();

    if (goalComposerActive) {
      try {
        const sid = await ensureGoalSession(prompt);
        const snapshot = await api.createGoal(sid, { objective: prompt });
        goalOpenRequestRef.current += 1;
        setGoalSnapshot(snapshot);
        setGoalComposerActive(false);
        toast.success(t('agent.researchGoalAttached'));
        const kickoff = goalKickoffPrompt(prompt);
        act().addMessage({
          id: "",
          type: "user",
          content: prompt,
          meta: { goalMode: true, requestText: kickoff },
          timestamp: Date.now(),
        });
        act().startActivity(`pending-${Date.now()}`);
        act().setStatus("streaming");
        forceScrollToBottom();
        setupSSE(sid);
        const sent = await api.sendMessage(sid, kickoff);
        if (act().activity?.attemptId.startsWith("pending-")) {
          act().setActivityAttemptId(sent.attempt_id);
        }
        void syncCompletedAttempt(sid, sent.attempt_id);
      } catch (error) {
        if (act().activity) archiveActivity("failed");
        act().setStatus("idle");
        const message = error instanceof Error ? error.message : t('agent.failedToStartGoal');
        toast.error(message);
        act().addMessage({ id: "", type: "error", content: message, timestamp: Date.now() });
      }
      return;
    }

    let finalPrompt = prompt;
    const messageMeta: AgentMessageMeta = {};

    // Swarm mode: let agent auto-select the right preset
    if (swarmPreset) {
      messageMeta.swarmMode = true;
      setSwarmPreset(null);
      finalPrompt = `${SWARM_PROMPT_PREFIX}${prompt}`;
    }

    if (attachment) {
      messageMeta.attachment = { filename: attachment.filename };
      finalPrompt = `[Uploaded file: ${attachment.filename}, path: ${attachment.filePath}]\n\n${finalPrompt}`;
    }
    messageMeta.requestText = finalPrompt;

    const widgetContext = formatTradeWidgetContextBlock();
    const displayPrompt = toDisplayPrompt(finalPrompt);
    const apiPrompt = widgetContext ? `${widgetContext}\n\n${finalPrompt}` : finalPrompt;

    setInput("");
    act().addMessage({
      id: "",
      type: "user",
      content: isTradeWidgetModified(getTradeWidgetAdjustment())
        ? `${displayPrompt.content}\n\n_(includes your trade plan leg adjustments)_`
        : displayPrompt.content,
      meta: messageMeta,
      timestamp: Date.now(),
    });
    act().setStatus("streaming");
    forceScrollToBottom();

    try {
      let sid = act().sessionId;
      if (!sid) {
        const session = await api.createSession(prompt.slice(0, 50));
        sid = session.session_id;
        act().setSessionId(sid);
        setSearchParams({ session: sid }, { replace: true });
      }
      setupSSE(sid);
      const sent = await api.sendMessage(sid, apiPrompt);
      if (act().activity?.attemptId.startsWith("pending-")) {
        act().setActivityAttemptId(sent.attempt_id);
      }
      void syncCompletedAttempt(sid, sent.attempt_id);
    } catch (error) {
      archiveActivity("failed");
      act().setStatus("error");
      const message = isAuthRequiredError(error) ? AUTH_REQUIRED_MESSAGE : t('agent.failedToSend');
      toast.error(message);
      act().addMessage({ id: "", type: "error", content: message, timestamp: Date.now() });
    }
  }, [
    archiveActivity,
    clearStreamingView,
    ensureGoalSession,
    forceScrollToBottom,
    goalComposerActive,
    setSearchParams,
    setupSSE,
    status,
    swarmPreset,
    syncCompletedAttempt,
    t,
  ]);

  const submitComposerPrompt = useCallback((prompt: string) => {
    composerRef.current?.submit(prompt);
  }, []);

  const handleCancel = useCallback(async () => {
    if (!sessionId) {
      flushPendingStreamUpdate();
      const partialAnswer = act().streamingText;
      const stoppedActivity = archiveActivity("stopped");
      act().clearStreaming();
      persistPartialAnswer(stoppedActivity, partialAnswer);
      act().setStatus("idle");
      act().clearStreaming();
      return;
    }
    try {
      await api.cancelSession(sessionId);
      flushPendingStreamUpdate();
      const partialAnswer = act().streamingText;
      const stoppedActivity = archiveActivity("stopped");
      act().clearStreaming();
      persistPartialAnswer(stoppedActivity, partialAnswer);
      act().setStatus("idle");
      toast.info(t('agent.cancelRequestSent'));
    } catch {
      // The attempt is still live. Preserve its activity, buffered text and
      // streaming status so a failed cancel request does not strand the turn.
      toast.error(t('agent.cancelFailed'));
    }
  }, [
    archiveActivity,
    flushPendingStreamUpdate,
    persistPartialAnswer,
    sessionId,
    t,
  ]);

  useEffect(() => {
    if (status !== "streaming") return;
    const cancelOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.repeat) return;
      event.preventDefault();
      void handleCancel();
    };
    document.addEventListener("keydown", cancelOnEscape);
    return () => document.removeEventListener("keydown", cancelOnEscape);
  }, [handleCancel, status]);

  const handleContinueGoal = useCallback(async () => {
    if (!sessionId || !goalSnapshot || status === "streaming") return;
    const prompt = goalContinuePrompt(goalSnapshot);
    act().addMessage({
      id: "",
      type: "user",
      content: t("agent.continueGoalMessage" as never, { goal: goalSnapshot.goal.objective }),
      meta: { goalMode: true, requestText: prompt },
      timestamp: Date.now(),
    });
    act().startActivity(`pending-${Date.now()}`);
    act().setStatus("streaming");
    forceScrollToBottom();
    composerRef.current?.focus();
    try {
      setupSSE(sessionId);
      const sent = await api.sendMessage(sessionId, prompt);
      if (act().activity?.attemptId.startsWith("pending-")) {
        act().setActivityAttemptId(sent.attempt_id);
      }
      void syncCompletedAttempt(sessionId, sent.attempt_id);
    } catch (error) {
      archiveActivity("failed");
      act().setStatus("error");
      const message = isAuthRequiredError(error) ? AUTH_REQUIRED_MESSAGE : t('agent.failedToContinue');
      toast.error(message);
      act().addMessage({ id: "", type: "error", content: message, timestamp: Date.now() });
    }
  }, [archiveActivity, forceScrollToBottom, goalSnapshot, sessionId, setupSSE, status, syncCompletedAttempt, t]);

  const handleHaltLive = useCallback(async () => {
    if (halting) return;
    setHalting(true);
    try {
      // The kill switch is global and must fire even with no active chat session
      // (e.g. a runner started from the CLI / another session). The backend scopes
      // the SSE broadcast by session_id when present; an empty string is a valid
      // global trip.
      await api.haltLive(sessionId ?? undefined);
      // Preemptive halt: the server trips the kill switch (cancel resting orders +
      // optional flatten per SPEC §7.5 #6) and broadcasts live.halted. Reflect
      // optimistically and re-poll the runtime panel so the runner shows stopped.
      setLiveHalted((cur) => cur ?? { broker: null, by: "frontend", tripped_at: new Date().toISOString() });
      setLiveStatusRefresh((n) => n + 1);
      toast.success(t('agent.connectorHalted'));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('agent.failedToHaltConnector'));
    } finally {
      setHalting(false);
    }
  }, [sessionId, halting]);

  const handleCancelGoal = useCallback(async () => {
    if (!sessionId || !goalSnapshot) return;
    try {
      await api.updateGoalStatus(sessionId, {
        goal_id: goalSnapshot.goal.goal_id,
        expected_goal_id: goalSnapshot.goal.goal_id,
        status: "cancelled",
        recap: "Cancelled from Web UI.",
      });
      setGoalSnapshot(null);
      setGoalDetailsOpen(false);
      toast.success(t('agent.researchGoalCancelled'));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('agent.failedToCancelGoal'));
    }
  }, [goalSnapshot, sessionId]);

  const handleStartGoalEdit = useCallback(() => {
    if (!goalSnapshot) return;
    setGoalEditValue(goalSnapshot.goal.objective);
    setGoalEditActive(true);
  }, [goalSnapshot]);

  const handleGoalSnapshotChange = useCallback((snapshot: GoalSnapshot | null) => {
    setGoalSnapshot(snapshot);
  }, []);

  const handleSaveGoalEdit = useCallback(async () => {
    const objective = goalEditValue.trim();
    if (!sessionId || !goalSnapshot || !objective) return;
    try {
      const response = await api.updateGoal(sessionId, {
        goal_id: goalSnapshot.goal.goal_id,
        expected_goal_id: goalSnapshot.goal.goal_id,
        objective,
      });
      handleGoalSnapshotChange(response.snapshot);
      setGoalEditActive(false);
      toast.success(t('agent.researchGoalUpdated'));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('agent.failedToUpdateGoal'));
    }
  }, [goalEditValue, goalSnapshot, handleGoalSnapshotChange, sessionId]);

  const handleRetry = useCallback((errorMsg: AgentMessage) => {
    if (status === "streaming") return;
    const msgs = act().messages;
    const errorIdx = msgs.findIndex(m => m.id === errorMsg.id);
    if (errorIdx === -1) return;
    // Find the most recent user message before this error
    let userContent: string | null = null;
    for (let i = errorIdx - 1; i >= 0; i--) {
      if (msgs[i].type === "user") {
        userContent = msgs[i].meta?.requestText || msgs[i].content;
        break;
      }
    }
    if (!userContent) return;
    submitComposerPrompt(userContent);
  }, [status, submitComposerPrompt]);

  const handleContinueActivity = useCallback(() => {
    submitComposerPrompt(t("agent.continuePrompt" as never));
  }, [submitComposerPrompt, t]);

  const handleReattachActivity = useCallback((timedOut: AgentActivity) => {
    if (!sessionId || status === "streaming") return;
    useAgentStore.setState((state) => ({
      messages: state.messages.filter((message) => (
        message.meta?.activity?.attemptId !== timedOut.attemptId
        && message.meta?.partialAttemptId !== timedOut.attemptId
      )),
      activity: {
        ...timedOut,
        state: "thinking",
        steps: [],
        endedAt: undefined,
      },
      toolCalls: [],
      streamingText: "",
    }));
    act().setStatus("streaming");
    sseSessionRef.current = null;
    setupSSE(sessionId);
    forceScrollToBottom();
  }, [forceScrollToBottom, sessionId, setupSSE, status]);

  const handleStartGoal = useCallback(() => {
    setSwarmPreset(null);
    setGoalComposerActive(true);
  }, []);

  const handleCancelGoalComposer = useCallback(() => {
    setGoalComposerActive(false);
  }, []);

  const handleStartSwarm = useCallback(() => {
    setGoalComposerActive(false);
    setSwarmPreset({ name: "auto", title: "Agent Swarm" });
  }, []);

  const handleCancelSwarm = useCallback(() => {
    setSwarmPreset(null);
  }, []);

  const handleExport = useCallback(() => {
    if (messages.length === 0) return;
    const lines: string[] = [`# Chat Export`, ``, `Export time: ${new Date().toLocaleString()}`, ``];
    for (const msg of messages) {
      const time = new Date(msg.timestamp).toLocaleString();
      if (msg.type === "user") {
        lines.push(`## User (${time})`, ``, msg.content, ``);
      } else if (msg.type === "answer") {
        lines.push(`## Assistant (${time})`, ``, msg.content, ``);
      } else if (msg.type === "error") {
        lines.push(`## Error (${time})`, ``, msg.content, ``);
      } else if (msg.type === "tool_call") {
        lines.push(`> Tool call: ${msg.tool || "unknown"}`, ``);
      } else if (msg.type === "swarm_status") {
        const swarmStatus = msg.swarmRunId ? swarmRuns[msg.swarmRunId] : msg.swarmStatus;
        lines.push(`> Swarm status: ${swarmStatus?.preset || "swarm"} ${swarmStatus?.status || ""}`, ``);
      } else if (msg.type === "run_complete") {
        lines.push(`> Backtest complete: ${msg.runId || ""}`, ``);
      }
    }
    const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `chat_${new Date().toISOString().slice(0, 10)}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }, [messages, swarmRuns]);

  const handleFileSelect = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";
    const blockedExts = [
      ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".app", ".dmg",
      ".so", ".dll", ".dylib",
      ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz",
    ];
    const lowered = file.name.toLowerCase();
    if (blockedExts.some((ext) => lowered.endsWith(ext))) {
      toast.error(t('agent.executablesNotAllowed'));
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      toast.error(t('agent.fileSizeExceeds'));
      return;
    }
    setUploading(true);
    setShowUploadMenu(false);
    try {
      const result = await api.uploadFile(file);
      setAttachment({ filename: result.filename, filePath: result.file_path });
      toast.success(t('agent.uploaded', { filename: result.filename }));
    } catch (err) {
      toast.error(t('agent.uploadFailed', { error: err instanceof Error ? err.message : 'Unknown error' }));
    } finally {
      setUploading(false);
    }
  }, [t]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (uploadMenuRef.current && !uploadMenuRef.current.contains(event.target as Node)) {
        setShowUploadMenu(false);
      }
    };
    if (showUploadMenu) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [showUploadMenu]);

  const hasCompletedTurn = useMemo(() => {
    let latestUserIndex = -1;
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (messages[index].type === "user") {
        latestUserIndex = index;
        break;
      }
    }
    return latestUserIndex >= 0 && messages
      .slice(latestUserIndex + 1)
      .some((message) => message.type === "answer" || message.type === "run_complete");
  }, [messages]);

  useEffect(() => {
    visibleRowsSessionRef.current = sessionId;
    setVisibleRowCount(TIMELINE_WINDOW_SIZE);
  }, [sessionId]);

  const groups = useMemo(() => groupMessages(messages), [messages]);
  const goalProgress = useMemo(() => getGoalProgress(goalSnapshot), [goalSnapshot]);

  /* Merge message groups with live-channel items, ordered by timestamp, so a
   * mandate proposal / live-action chip renders inline at the point it arrived. */
  type TimelineRow =
    | { sort: number; render: "group"; group: MsgGroup; key: string }
    | { sort: number; render: "live"; item: LiveItem; key: string };
  const timelineRows = useMemo<TimelineRow[]>(() => {
    const rows: TimelineRow[] = groups.map((g, i) => {
      const ts = g.kind === "timeline" ? g.msgs[0].timestamp : g.msg.timestamp;
      const key = g.kind === "timeline" ? `g_${g.msgs[0].id || g.msgs[0].timestamp}` : `g_${g.msg.id || g.msg.timestamp}_${i}`;
      return { sort: ts, render: "group", group: g, key };
    });
    for (const item of liveItems) {
      const key = item.kind === "proposal"
        ? `lp_${item.proposal.proposal_id}`
        : item.kind === "autonomous_proposal"
          ? `ap_${item.proposal.proposal_id}`
        : item.kind === "trade_plan_widget"
          ? `tw_${item.widget.widget_id}`
          : item.kind === "agent_audit"
            ? `aa_${item.audit.audit_id || item.timestamp}`
          : `la_${item.action.audit_id || item.timestamp}`;
      rows.push({ sort: item.timestamp, render: "live", item, key });
    }
    return rows.sort((a, b) => a.sort - b.sort);
  }, [groups, liveItems]);

  const visibleCountForSession = visibleRowsSessionRef.current === sessionId
    ? visibleRowCount
    : TIMELINE_WINDOW_SIZE;
  const visibleTimelineStart = Math.max(0, timelineRows.length - visibleCountForSession);
  const visibleTimelineRows = timelineRows.slice(visibleTimelineStart);
  const mountRowSessionRef = useRef<string | null | undefined>(undefined);
  const mountRowCountRef = useRef<number | null>(null);
  if (mountRowSessionRef.current !== sessionId) {
    mountRowSessionRef.current = sessionId;
    mountRowCountRef.current = sessionLoading ? null : timelineRows.length;
  } else if (mountRowCountRef.current === null && !sessionLoading) {
    mountRowCountRef.current = timelineRows.length;
  }

  /* Whether connector runtime activity could be active *anywhere* — the global kill switch must be
   * available whenever it could (audit M2 / SPEC Consent §4). Driven off both
   * in-session SSE artifacts AND the shared `/live/status` snapshot, so a runner
   * started from the CLI or another browser session still surfaces the halt button
   * in a freshly-loaded web session. */
  const selectedConnectorProfile = tradingConnectors?.profiles.find((p) => p.selected) ?? null;
  const oauthRuntimeEngaged = liveStatus != null && liveStatus.brokers.some((b) => (
    b.auth.oauth_token_present || b.runner?.alive || b.mandate != null
  ));
  const showOAuthRuntime = selectedConnectorProfile?.transport === "remote_mcp" || oauthRuntimeEngaged;

  const liveStatusActive =
    liveStatus != null &&
    showOAuthRuntime &&
    (liveStatus.global_halted ||
      liveStatus.brokers.some((b) => b.auth.oauth_token_present || b.runner?.alive || b.mandate != null));
  const liveActive =
    liveItems.length > 0 ||
    Object.keys(committedMandates).length > 0 ||
    liveHalted != null ||
    liveStatusActive;
  const liveIsHalted =
    showOAuthRuntime &&
    (isGlobalLiveHalt(liveHalted) || (liveStatus?.global_halted ?? false));

  const handleSubmit = useCallback((event: FormEvent) => {
    event.preventDefault();
    runPrompt(input);
  }, [input, runPrompt]);

  return (
    <>
    <div className="flex flex-col flex-1 min-w-0 overflow-hidden h-full">
      <ModelRuntimeBar
        settings={llmSettings}
        runtimeProvider={visibleRuntimeIdentity.provider}
        runtimeModel={visibleRuntimeIdentity.model}
        runtimeReasoningEffort={visibleRuntimeIdentity.reasoningEffort}
      />
      <div
        ref={listRef}
        data-streaming={status === "streaming" ? "true" : undefined}
        className="chat-scroll-container flex-1 overflow-auto p-6 relative"
      >
        <div
          className={[
            "max-w-3xl mx-auto space-y-8",
            !sessionLoading && messages.length === 0 ? "min-h-full flex flex-col" : "",
          ].join(" ")}
        >
          {sessionLoading && (
            <div className="space-y-4 py-4">
              {[1, 2, 3].map(i => (
                <div key={i} className="flex gap-3 animate-pulse">
                  <div className="h-8 w-8 rounded-full bg-muted shrink-0" />
                  <div className="flex-1 space-y-2">
                    <div className="h-4 bg-muted rounded w-3/4" />
                    <div className="h-3 bg-muted/60 rounded w-1/2" />
                  </div>
                </div>
              ))}
            </div>
          )}
          {sessionLoadError && (
            <div className="rounded-xl border border-red-500/40 bg-red-500/5 px-4 py-3 text-sm text-red-800 dark:text-red-200">
              <p className="font-medium">Could not load session messages</p>
              <p className="mt-1 text-xs opacity-90">{sessionLoadError}</p>
              {sessionId && (
                <button
                  type="button"
                  onClick={() => void refreshSessionMessages(sessionId)}
                  className="mt-2 rounded-lg border border-red-600/30 px-2.5 py-1 text-xs font-medium hover:bg-red-500/10"
                >
                  Retry
                </button>
              )}
            </div>
          )}
          {!sessionLoading && messages.length === 0 && (
            isDraftCreateView ? (
              <OrchestratorWelcome onExample={runPrompt} />
            ) : isEmbeddedAutonomousView ? (
              <AutonomousSessionEmptyState
                agent={autonomousAgent}
                agentId={autonomousAgentId ?? urlAgentId}
                loadState={autonomousAgentLoadState}
              />
            ) : (
              <WelcomeScreen onExample={runPrompt} />
            )
          )}

          {isDraftCreateView && orchestratorMissingProposal && (
            <div className="rounded-xl border border-amber-500/40 bg-amber-500/5 px-4 py-3 text-xs text-amber-900 dark:text-amber-100">
              <p className="font-medium">No proposal card yet</p>
              <p className="mt-1 text-amber-800/90 dark:text-amber-200/90">
                The orchestrator must call <span className="font-mono">propose_autonomous_agent</span> to create the
                confirmation card. Prose alone does not render a card.
              </p>
              <button
                type="button"
                onClick={() => {
                  setOrchestratorMissingProposal(false);
                  runPrompt("Please call propose_autonomous_agent with the settings we discussed.");
                }}
                className="mt-2 rounded-lg border border-amber-600/30 px-2.5 py-1 text-[11px] font-medium hover:bg-amber-500/10"
              >
                Ask to create the card
              </button>
            </div>
          )}

          {visibleTimelineRows.map((row, visibleRowIdx) => {
            const rowIdx = visibleTimelineStart + visibleRowIdx;
            const shouldAnimate = (
              mountRowCountRef.current !== null &&
              rowIdx >= mountRowCountRef.current
            );
            if (row.render === "live") {
              if (row.item.kind === "proposal") {
                return (
                  <MandateProposalCard
                    key={row.key}
                    proposal={row.item.proposal}
                    committed={committedMandates[row.item.proposal.proposal_id] ?? null}
                    onAdjust={runPrompt}
                  />
                );
              }
              if (row.item.kind === "autonomous_proposal") {
                const ap = row.item;
                return (
                  <AutonomousAgentProposalCard
                    key={row.key}
                    proposal={ap.proposal}
                    committed={committedAutonomous[ap.proposal.proposal_id] ?? null}
                    onAdjust={runPrompt}
                    onDismiss={() => {
                      setLiveItems((items) =>
                        items.filter(
                          (item) =>
                            !(
                              item.kind === "autonomous_proposal" &&
                              item.proposal.proposal_id === ap.proposal.proposal_id
                            ),
                        ),
                      );
                    }}
                    onCommitted={(agentId, sessionId) => {
                      setCommittedAutonomous((prev) => ({
                        ...prev,
                        [ap.proposal.proposal_id]: { agent_id: agentId, name: ap.proposal.name },
                      }));
                      onAutonomousAgentCommitted?.(agentId, sessionId);
                    }}
                  />
                );
              }
              if (row.item.kind === "trade_plan_widget") {
                const approvalState = widgetApprovalState(row.item.widget, autonomousAgent);
                return (
                  <TradePlanWidgetCard
                    key={row.key}
                    widget={row.item.widget}
                    autonomousMode={isEmbeddedAutonomousView}
                    approvalState={approvalState}
                    agentId={autonomousAgentId ?? urlAgentId ?? undefined}
                    onPlanApprovalChange={onAutonomousAgentRefresh}
                  />
                );
              }
              if (row.item.kind === "agent_audit") {
                return <AgentAuditChip key={row.key} audit={row.item.audit} />;
              }
              return <LiveActionChip key={row.key} action={row.item.action} />;
            }
            const g = row.group;
            if (g.kind === "timeline") {
              const isLastRow = rowIdx === timelineRows.length - 1;
              return (
                <div
                  key={row.key}
                  data-msg-idx={g.msgIdx}
                  className={shouldAnimate ? "msg-enter" : undefined}
                >
                  <ThinkingTimeline
                    messages={g.msgs}
                    isLatest={isLastRow && status === "streaming"}
                    onContinue={handleContinueActivity}
                    onReattach={handleReattachActivity}
                  />
                </div>
              );
            }
            const swarmStatus = g.msg.swarmRunId
              ? swarmRuns[g.msg.swarmRunId] ?? g.msg.swarmStatus
              : g.msg.swarmStatus;
            if (g.msg.type === "swarm_status" && swarmStatus) {
              return (
                <div
                  key={row.key}
                  data-msg-idx={g.msgIdx}
                  className={shouldAnimate ? "msg-enter" : undefined}
                >
                  <SwarmStatusCard status={swarmStatus} />
                </div>
              );
            }
            return (
              <div
                key={row.key}
                data-msg-idx={g.msgIdx}
                className={shouldAnimate ? "msg-enter" : undefined}
              >
                <MessageBubble msg={g.msg} onRetry={g.msg.type === "error" ? handleRetry : undefined} />
              </div>
            );
          })}

          {/* Streaming area - single stable wrapper to prevent insertBefore DOM race */}
          {status === "streaming" && (
            <div className="flex gap-3 msg-enter">
              <AgentAvatar />
              <div className="flex-1 min-w-0 space-y-2">
                {activity && <ActivityLine activity={activity} reasoningTail={reasoningTail} />}
                {streamingText && (
                  <div aria-live="polite" aria-atomic="false">
                    <MarkdownContent content={streamingText} streaming showCursor />
                  </div>
                )}
                {reasoningActive && !streamingText && (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Loader2 className="h-3 w-3 animate-spin text-primary shrink-0" />
                    <span>{t('agent.reasoning')}</span>
                  </div>
                )}
                {streamingText && (
                  <MarkdownContent content={throttledStreamingText} showCursor />
                )}
                {toolCalls.length > 0 && (
                  <ToolProgressIndicator toolCalls={toolCalls} />
                )}
              </div>
            </div>
          )}

          {/* Persistent streaming pulse bar — always visible while agent is working */}
          {status === "streaming" && (
            <div className="flex items-center gap-2 px-1 pt-1">
              <div className="h-0.5 flex-1 rounded-full bg-primary/20 overflow-hidden">
                <div className="h-full w-1/3 bg-primary rounded-full animate-[pulse-slide_2s_ease-in-out_infinite]" />
              </div>
              <span className="text-[10px] text-muted-foreground shrink-0 tabular-nums">{t('agent.running')}</span>
            </div>
          )}

        </div>

        {/* Scroll to bottom button */}
        {showScrollBtn && (
          <button
            onClick={() => forceScrollToBottom(true)}
            className="sticky bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-1 px-3 py-1.5 rounded-full bg-primary text-primary-foreground text-xs font-medium shadow-lg hover:opacity-90 transition-opacity z-10"
          >
            <ArrowDown className="h-3 w-3" /> {t('agent.newMessages')}
          </button>
        )}
        <ConversationTimeline messages={messages} containerRef={listRef} />
      </div>

      <form onSubmit={handleSubmit} data-agent-composer className="border-t p-4 bg-background/80 backdrop-blur-sm">
        <div className="max-w-3xl mx-auto space-y-2">
          {/* Swarm preset badge */}
          {swarmPreset && (
            <div className="flex items-center gap-1">
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-violet-500/10 text-violet-600 dark:text-violet-400 text-xs font-medium">
                <Users className="h-3 w-3" />
                {swarmPreset.title}
                <button type="button" onClick={handleCancelSwarm} className="hover:text-destructive transition-colors">
                  <X className="h-3 w-3" />
                </button>
              </span>
            </div>
          )}
          {goalComposerActive && (
            <div className="flex items-center gap-1">
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-primary/10 text-primary text-xs font-medium">
                <Target className="h-3 w-3" />
                {t("agent.newResearchGoal")}
                <button type="button" onClick={handleCancelGoalComposer} className="hover:text-destructive transition-colors">
                  <X className="h-3 w-3" />
                </button>
              </span>
            </div>
          )}
          {goalSnapshot && !goalComposerActive && (
            <div className="grid gap-2">
              <button
                type="button"
                onClick={() => setGoalDetailsOpen((open) => !open)}
                className="inline-flex max-w-full items-center gap-1.5 justify-self-start rounded-lg bg-primary/10 px-2.5 py-1 text-left text-xs font-medium text-primary transition-colors hover:bg-primary/15"
                title={goalSnapshot.goal.objective}
                aria-label={t("agent.activeResearchGoal")}
                aria-expanded={goalDetailsOpen}
              >
                <Target className="h-3 w-3 shrink-0" />
                <span className="shrink-0">{t('agent.goal')}</span>
                <span className="truncate text-muted-foreground">
                  {goalSnapshot.goal.ui_summary || goalSnapshot.goal.objective}
                </span>
                {goalProgress.metLabel && (
                  <span className="shrink-0 font-mono text-[11px] text-emerald-600 dark:text-emerald-400">
                    {goalProgress.metLabel}
                  </span>
                )}
                {goalProgress.evidenceTotal > 0 && (
                  <span className="shrink-0 rounded bg-background px-1 font-mono text-[10px] text-primary" title={t("agent.evidenceCollectedTitle")}>
                    {t("agent.evidenceCount", { count: goalProgress.evidenceTotal })}
                  </span>
                )}
                <ChevronDown
                  className={[
                    "h-3 w-3 shrink-0 transition-transform",
                    goalDetailsOpen ? "rotate-180" : "",
                  ].join(" ")}
                  aria-hidden="true"
                />
              </button>
              {goalDetailsOpen && (
                <div className="grid gap-3 rounded-xl border border-primary/20 bg-background/95 p-3 text-xs shadow-sm">
                  {goalEditActive ? (
                    <div className="grid gap-2">
                      <textarea
                        value={goalEditValue}
                        onChange={(event) => setGoalEditValue(event.target.value)}
                        rows={3}
                        aria-label={t("agent.editGoalObjectiveLabel")}
                        className="w-full rounded-lg border bg-background px-3 py-2 text-xs leading-relaxed text-foreground outline-none focus:ring-2 focus:ring-primary/30"
                      />
                      <div className="flex justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => setGoalEditActive(false)}
                          className="inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:text-foreground"
                        >
                          <X className="h-3 w-3" />
                          {t("agent.cancel")}
                        </button>
                        <button
                          type="button"
                          onClick={handleSaveGoalEdit}
                          disabled={!goalEditValue.trim()}
                          className="inline-flex items-center gap-1 rounded-lg bg-primary px-2 py-1 text-[11px] font-medium text-primary-foreground transition-opacity disabled:opacity-40"
                        >
                          <Check className="h-3 w-3" />
                          {t("agent.save")}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="rounded-lg border bg-muted/20 px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
                      {goalSnapshot.goal.objective}
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-2">
                    <div className="rounded-lg border bg-muted/20 p-2.5">
                      <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                        {t("agent.criteria")}
                      </div>
                      <div className="mt-1 font-mono text-base font-semibold text-foreground">
                        {goalProgress.label || "0/0"}
                      </div>
                    </div>
                    <div className="rounded-lg border bg-muted/20 p-2.5">
                      <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                        {t("agent.evidence")}
                      </div>
                      <div className="mt-1 font-mono text-base font-semibold text-foreground">
                        {goalProgress.evidenceTotal}
                      </div>
                    </div>
                  </div>
                  <div className="grid gap-1.5">
                    {goalSnapshot.criteria.map((criterion, index) => {
                      const evidenceCount = criterionEvidenceCount(goalSnapshot, criterion.criterion_id);
                      const displayStatus = criterionCovered(goalSnapshot, criterion) && !isCriterionStatusMet(criterion.status)
                        ? "covered"
                        : statusLabel(criterion.status);
                      return (
                        <div
                          key={criterion.criterion_id}
                          className="grid grid-cols-[1.25rem_minmax(0,1fr)_auto] items-start gap-2 rounded-lg border bg-muted/20 p-2"
                        >
                          <span className="flex h-5 w-5 items-center justify-center rounded-full bg-muted text-[10px] text-muted-foreground">
                            {criterionIndexLabel(index)}
                          </span>
                          <span className="min-w-0">
                            <span className="block truncate font-medium text-foreground">{criterion.text}</span>
                            <span className="block text-[11px] text-muted-foreground">
                              {displayStatus}
                            </span>
                          </span>
                          <span className="rounded-full border px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                            {evidenceCount} ev
                          </span>
                        </div>
                      );
                    })}
                  </div>
                  {goalSnapshot.evidence.length > 0 && (
                    <div className="grid gap-1.5 border-t pt-2">
                      <div className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                        {t("agent.recentEvidence")}
                      </div>
                      {latestGoalEvidence(goalSnapshot).map((item) => (
                        <div key={item.evidence_id} className="rounded-lg bg-muted/20 px-2 py-1.5">
                          <div className="mb-0.5 flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
                            <span className="truncate">{item.source_provider || "evidence"}</span>
                            <span>{statusLabel(item.verification_status)}</span>
                          </div>
                          <div className="line-clamp-2 text-[11px] leading-relaxed text-foreground">
                            {item.text}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="flex flex-wrap justify-end gap-2 border-t pt-2">
                    <button
                      type="button"
                      onClick={handleContinueGoal}
                      disabled={status === "streaming"}
                      className="inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:text-foreground disabled:opacity-40"
                    >
                      <Play className="h-3 w-3" />
                      {t("agent.continue")}
                    </button>
                    <button
                      type="button"
                      onClick={handleStartGoalEdit}
                      disabled={goalEditActive}
                      className="inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:text-foreground disabled:opacity-40"
                    >
                      <Pencil className="h-3 w-3" />
                      {t("agent.edit")}
                    </button>
                    <button
                      type="button"
                      onClick={handleCancelGoal}
                      className="inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:border-destructive/40 hover:text-destructive"
                    >
                      <X className="h-3 w-3" />
                      {t("agent.cancelGoal")}
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
          {/* Persistent live runtime status panel — sits alongside the goal/mandate
              badges (SPEC §7.5 + audit C2). Self-hides when no broker is configured. */}
          <RunnerStatus
            status={liveStatus}
            connectors={tradingConnectors}
            connectorCheck={connectorCheck}
            unavailable={liveStatusUnavailable}
            halted={liveIsHalted}
            onRefresh={refreshConnectorRuntime}
          />
          {/* Attachment badge */}
          {attachment && (
            <div className="flex items-center gap-1">
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-primary/10 text-primary text-xs font-medium">
                <Paperclip className="h-3 w-3" />
                {attachment.filename}
                <button type="button" onClick={() => setAttachment(null)} className="hover:text-destructive transition-colors">
                  <X className="h-3 w-3" />
                </button>
              </span>
            </div>
          )}
          {/* Uploading indicator */}
          {uploading && (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              {t("agent.uploading")}
            </div>
          )}
          {/* Persistent kill switch — distinct from the per-turn Stop button
              above; disables all live order activity (SPEC Consent §4).
              Given a top border + extra top padding so this safety-critical
              control visually separates from the lighter decorative chips
              (swarm/goal/attachment) stacked above it. */}
          {liveActive && showOAuthRuntime && (
            <div className="flex items-center gap-2 border-t border-border/60 pt-2">
              {liveIsHalted ? (
                <span className="inline-flex items-center gap-1.5 rounded-lg bg-destructive/10 px-2.5 py-1 text-xs font-medium text-destructive">
                  <OctagonX className="h-3 w-3" />
                  {t("agent.oauthRuntimeHalted")}
                </span>
              ) : (
                <button
                  type="button"
                  onClick={handleHaltLive}
                  disabled={halting}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-destructive/40 bg-destructive/5 px-2.5 py-1 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-40"
                  title={t("agent.haltConnectorTitle")}
                >
                  {halting ? <Loader2 className="h-3 w-3 animate-spin" /> : <OctagonX className="h-3 w-3" />}
                  {t("agent.haltConnector")}
                </button>
              )}
            </div>
          )}
          <div className="flex gap-2 items-end">
            {/* "+" menu: PDF upload + Swarm presets */}
            <div className="relative" ref={uploadMenuRef}>
              <button
                type="button"
                onClick={() => setShowUploadMenu(prev => !prev)}
                disabled={status === "streaming" || uploading}
                className="w-9 h-9 rounded-full border flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-40 shrink-0"
                title={t("agent.moreOptions")}
              >
                <Plus className="h-4 w-4" />
              </button>
              {showUploadMenu && (
                <div className="absolute bottom-full left-0 mb-2 w-52 rounded-xl border bg-background/95 backdrop-blur-sm shadow-lg py-1 z-50">
                  <button
                    type="button"
                    onClick={() => { fileInputRef.current?.click(); setShowUploadMenu(false); }}
                    className="w-full px-3 py-2 text-left text-sm hover:bg-muted transition-colors flex items-center gap-2"
                  >
                    <Paperclip className="h-4 w-4" />
                    {t("agent.uploadPdf")}
                  </button>
                  <div className="border-t my-1" />
                  <button
                    type="button"
                    onClick={() => {
                      setShowUploadMenu(false);
                      handleStartGoal();
                      inputRef.current?.focus();
                    }}
                    className="w-full px-3 py-2 text-left text-sm hover:bg-muted transition-colors flex items-center gap-2"
                  >
                    <Target className="h-4 w-4" />
                    {t("agent.researchGoal")}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setShowUploadMenu(false);
                      handleStartSwarm();
                      inputRef.current?.focus();
                    }}
                    className="w-full px-3 py-2 text-left text-sm hover:bg-muted transition-colors flex items-center gap-2"
                  >
                    <Users className="h-4 w-4" />
                    {t("agent.agentSwarm")}
                  </button>
                  <div className="border-t my-1" />
                  <button
                    type="button"
                    onClick={() => {
                      setShowUploadMenu(false);
                      void runPrompt(CONNECTOR_CHECK_PROMPT);
                    }}
                    className="w-full px-3 py-2 text-left text-sm hover:bg-muted transition-colors flex items-center gap-2"
                  >
                    <Landmark className="h-4 w-4" />
                    {t("agent.checkConnector")}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setShowUploadMenu(false);
                      void runPrompt(CONNECTOR_PORTFOLIO_PROMPT);
                    }}
                    className="w-full px-3 py-2 text-left text-sm hover:bg-muted transition-colors flex items-center gap-2"
                  >
                    <Landmark className="h-4 w-4" />
                    {t("agent.analyzePortfolio")}
                  </button>
                </div>
              )}
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.docx,.xlsx,.xls,.pptx,.csv,.tsv,.txt,.md,.log,.json,.yaml,.yml,.toml,.html,.xml,.rst,.png,.jpg,.jpeg,.gif,.bmp,.webp,.tiff"
              onChange={handleFileSelect}
              className="hidden"
            />
            <textarea
              ref={inputRef}
              value={input}
              rows={1}
              onChange={(e) => setInput(e.target.value)}
              onCompositionStart={() => {
                isComposingRef.current = true;
              }}
              onCompositionEnd={() => {
                isComposingRef.current = false;
                lastCompositionEndRef.current = Date.now();
              }}
              onInput={(e) => {
                const el = e.target as HTMLTextAreaElement;
                el.style.height = "auto";
                el.style.height = el.scrollHeight + "px";
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  const nativeEvent = e.nativeEvent as KeyboardEvent & { isComposing?: boolean };
                  const justFinishedComposing = Date.now() - lastCompositionEndRef.current < 80;
                  if (isComposingRef.current || nativeEvent.isComposing || nativeEvent.keyCode === 229) {
                    return;
                  }
                  if (justFinishedComposing) {
                    e.preventDefault();
                    return;
                  }
                  e.preventDefault();
                  runPrompt(input.trim());
                }
              }}
              placeholder={
                newsScenarioMode && pipelineStale
                  ? "Restart the advisor session to use the refreshed Analysis snapshot."
                  : goalComposerActive
                    ? t("agent.describeGoal")
                    : hasCompletedTurn
                      ? t("agent.followUpPlaceholder" as never)
                      : t("agent.placeholder")
              }
              aria-label={t("agent.messageInputLabel")}
              className="flex-1 px-4 py-2.5 rounded-xl border bg-background text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 transition-shadow resize-none max-h-32 overflow-y-auto"
              disabled={status === "streaming" || (newsScenarioMode && pipelineStale)}
            />
            {sessionId != null && (
              <button
                type="button"
                onClick={handleExport}
                disabled={messages.length === 0}
                className="h-9 w-9 rounded-full border flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-30 disabled:pointer-events-none shrink-0"
                title={t("agent.exportChat")}
              >
                <Download className="h-4 w-4" />
              </button>
            )}
            {status === "streaming" ? (
              <button
                type="button"
                onClick={handleCancel}
                className="h-9 px-4 rounded-full bg-destructive text-destructive-foreground text-sm font-medium hover:opacity-90 transition-opacity shrink-0"
                title={t("agent.stopGeneration")}
              >
                <Square className="h-4 w-4" />
              </button>
            ) : (
              <button
                type="submit"
                disabled={goalComposerActive ? !input.trim() : (!input.trim() && !attachment)}
                className="h-9 px-4 rounded-full bg-primary text-primary-foreground text-sm font-medium disabled:opacity-40 hover:opacity-90 transition-opacity shrink-0"
                title={t("agent.send")}
                aria-label={t("agent.send")}
              >
                <Send className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>
      </form>
    </div>
    <ContextDrawer
      sessionId={sessionId}
      agentId={
        isEmbeddedAutonomousView
          ? (autonomousAgentId ?? urlAgentId ?? undefined)
          : undefined
      }
      ticker={newsScenarioMode ? null : researchTicker}
      assetType={researchAssetType}
      planArtifact={newsScenarioMode ? null : planArtifact}
      debateArtifact={newsScenarioMode ? null : debateArtifact}
      debateRunning={newsScenarioMode ? false : debateRunning}
      debateError={newsScenarioMode ? null : debateError}
      onDebateUpdate={handleDebateUpdate}
    />
    </>
  );
}
