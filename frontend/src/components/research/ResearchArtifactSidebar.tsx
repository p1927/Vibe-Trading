import { PanelRightClose, PanelRightOpen } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { AgentDebatePanel } from "@/components/research/AgentDebatePanel";
import { ResearchContextPanel } from "@/components/research/ResearchContextPanel";
import { api, type HubPlanArtifact, type AgentDebateArtifact } from "@/lib/api";
import { cn } from "@/lib/utils";

const STORAGE_KEY = "vibe-research-panel";

interface Props {
  ticker: string | null;
  assetType?: string;
  planArtifact?: HubPlanArtifact | null;
  debateArtifact?: AgentDebateArtifact | null;
  debateRunning?: boolean;
  debateError?: string | null;
  onDebateUpdate?: (debate: AgentDebateArtifact | null, running: boolean, error?: string | null) => void;
}

export function ResearchArtifactSidebar({
  ticker,
  assetType = "options",
  planArtifact,
  debateArtifact,
  debateRunning,
  debateError,
  onDebateUpdate,
}: Props) {
  const [open, setOpen] = useState(() => localStorage.getItem(STORAGE_KEY) !== "collapsed");
  const [tab, setTab] = useState<"plan" | "debate">("plan");
  const [localPlan, setLocalPlan] = useState<HubPlanArtifact | null>(planArtifact ?? null);
  const [loadingPlan, setLoadingPlan] = useState(false);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, open ? "expanded" : "collapsed");
  }, [open]);

  useEffect(() => {
    if (planArtifact) setLocalPlan(planArtifact);
  }, [planArtifact]);

  useEffect(() => {
    if (!ticker || planArtifact) return;
    let cancelled = false;
    setLoadingPlan(true);
    api
      .getHubPlan(ticker, assetType)
      .then((res) => {
        if (cancelled) return;
        if (res.status === "ok" && res.artifact) setLocalPlan(res.artifact);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoadingPlan(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ticker, assetType, planArtifact]);

  const runDebate = useCallback(async () => {
    if (!ticker) return;
    onDebateUpdate?.(null, true, null);
    setTab("debate");
    try {
      const res = await api.runAgentDebate({ ticker, asset_type: assetType, refresh: true });
      if (res.status === "running") {
        onDebateUpdate?.(null, true, null);
        toast.info("Agent debate started — this may take a few minutes");
        return;
      }
      if (res.debate) {
        onDebateUpdate?.(res.debate, false, null);
        toast.success("Agent debate ready");
      } else {
        onDebateUpdate?.(null, false, res.message || "Debate failed");
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Debate request failed";
      onDebateUpdate?.(null, false, msg);
      toast.error(msg);
    }
  }, [ticker, assetType, onDebateUpdate]);

  if (!ticker) return null;

  return (
    <>
      {!open && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="absolute right-0 top-1/2 z-20 flex -translate-y-1/2 items-center gap-1 rounded-l-lg border border-r-0 bg-background px-1.5 py-3 text-[10px] font-medium text-muted-foreground shadow-sm hover:text-foreground"
          title="Show research panel"
        >
          <PanelRightOpen className="h-3.5 w-3.5" />
          <span className="[writing-mode:vertical-rl] rotate-180">Research</span>
        </button>
      )}

      <aside
        className={cn(
          "flex shrink-0 flex-col border-s bg-background/95 transition-[width] duration-200",
          open ? "w-80" : "w-0 overflow-hidden border-s-0",
        )}
      >
        {open && (
          <>
            <div className="flex items-center justify-between border-b px-3 py-2">
              <div className="min-w-0">
                <div className="truncate text-xs font-semibold">{ticker}</div>
                <div className="text-[10px] text-muted-foreground">Research artifacts</div>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                title="Collapse panel"
              >
                <PanelRightClose className="h-4 w-4" />
              </button>
            </div>

            <div className="flex border-b text-[11px]">
              <button
                type="button"
                onClick={() => setTab("plan")}
                className={cn(
                  "flex-1 px-3 py-2 font-medium transition",
                  tab === "plan" ? "border-b-2 border-emerald-500 text-foreground" : "text-muted-foreground",
                )}
              >
                Trade plan
              </button>
              <button
                type="button"
                onClick={() => setTab("debate")}
                className={cn(
                  "flex-1 px-3 py-2 font-medium transition",
                  tab === "debate" ? "border-b-2 border-violet-500 text-foreground" : "text-muted-foreground",
                )}
              >
                Agent debate
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-3 space-y-3">
              {tab === "plan" && (
                <>
                  {loadingPlan && !localPlan && (
                    <p className="text-[11px] text-muted-foreground">Loading hub research…</p>
                  )}
                  {localPlan && (
                    <ResearchContextPanel
                      underlying={localPlan.underlying || ticker}
                      artifact={localPlan}
                    />
                  )}
                  {!loadingPlan && localPlan && localPlan.plan_status === "incomplete" && (
                    <p className="text-[11px] text-muted-foreground">
                      Tip: ask to refresh the {ticker} plan in chat once OpenAlgo is running.
                    </p>
                  )}
                  {!loadingPlan && !localPlan && (
                    <p className="text-[11px] text-muted-foreground">
                      No hub plan yet — ask about strategies and the agent will generate one.
                    </p>
                  )}
                </>
              )}

              {tab === "debate" && (
                <AgentDebatePanel
                  ticker={ticker}
                  debate={debateArtifact ?? null}
                  running={debateRunning}
                  error={debateError}
                  onRunDebate={runDebate}
                />
              )}
            </div>
          </>
        )}
      </aside>
    </>
  );
}
