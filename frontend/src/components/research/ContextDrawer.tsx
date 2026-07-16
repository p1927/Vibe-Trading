import { Link2, PanelRightClose, PanelRightOpen } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { AgentDebatePanel } from "@/components/research/AgentDebatePanel";
import { ResearchContextPanel } from "@/components/research/ResearchContextPanel";
import { SourcesPanel } from "@/components/research/SourcesPanel";
import { api, type AgentDebateArtifact, type HubPlanArtifact } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useProvenanceStore, type ContextSection } from "@/stores/provenance";

const STORAGE_KEY = "vibe-context-panel";
const LEGACY_STORAGE_KEY = "vibe-research-panel";

const SECTIONS: { id: ContextSection; label: string }[] = [
  { id: "sources", label: "Sources" },
  { id: "research", label: "Research" },
  { id: "debate", label: "Debate" },
];

interface Props {
  sessionId: string | null;
  ticker: string | null;
  assetType?: string;
  planArtifact?: HubPlanArtifact | null;
  debateArtifact?: AgentDebateArtifact | null;
  debateRunning?: boolean;
  debateError?: string | null;
  onDebateUpdate?: (debate: AgentDebateArtifact | null, running: boolean, error?: string | null) => void;
}

export function ContextDrawer({
  sessionId,
  ticker,
  assetType = "options",
  planArtifact,
  debateArtifact,
  debateRunning,
  debateError,
  onDebateUpdate,
}: Props) {
  const drawerOpen = useProvenanceStore((s) => s.drawerOpen);
  const drawerWide = useProvenanceStore((s) => s.drawerWide);
  const activeSection = useProvenanceStore((s) => s.activeSection);
  const sources = useProvenanceStore((s) => s.sources);
  const setDrawerOpen = useProvenanceStore((s) => s.setDrawerOpen);
  const setActiveSection = useProvenanceStore((s) => s.setActiveSection);
  const setSources = useProvenanceStore((s) => s.setSources);

  const [localPlan, setLocalPlan] = useState<HubPlanArtifact | null>(planArtifact ?? null);
  const [loadingPlan, setLoadingPlan] = useState(false);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, drawerOpen ? "expanded" : "collapsed");
    if (drawerOpen) {
      try {
        localStorage.removeItem(LEGACY_STORAGE_KEY);
      } catch {
        /* ignore */
      }
    }
  }, [drawerOpen]);

  useEffect(() => {
    if (planArtifact) setLocalPlan(planArtifact);
  }, [planArtifact]);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    api
      .getSessionProvenance(sessionId)
      .then((res) => {
        if (!cancelled && res.sources?.length) setSources(res.sources);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [sessionId, setSources]);

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
    setActiveSection("debate");
    setDrawerOpen(true);
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
  }, [ticker, assetType, onDebateUpdate, setActiveSection, setDrawerOpen]);

  const sourceCount = sources.length;
  const panelWidth = drawerWide ? "w-[min(560px,45vw)]" : "w-80";

  const sectionSubtitle = useMemo(() => {
    if (activeSection === "sources") return `${sourceCount} data reference${sourceCount === 1 ? "" : "s"}`;
    if (activeSection === "research") return ticker ? `${ticker} trade plan` : "Hub research";
    return ticker ? `${ticker} agent debate` : "Multi-agent debate";
  }, [activeSection, sourceCount, ticker]);

  return (
    <>
      {!drawerOpen && (
        <button
          type="button"
          onClick={() => setDrawerOpen(true)}
          className="absolute right-0 top-1/2 z-30 flex -translate-y-1/2 items-center gap-1 rounded-l-lg border border-r-0 bg-background px-1.5 py-3 text-[10px] font-medium text-muted-foreground shadow-sm hover:text-foreground"
          title="Show context panel"
        >
          <PanelRightOpen className="h-3.5 w-3.5" />
          <span className="[writing-mode:vertical-rl] rotate-180">
            Context{sourceCount > 0 ? ` · ${sourceCount}` : ""}
          </span>
        </button>
      )}

      <aside
        className={cn(
          "flex shrink-0 flex-col border-s bg-background/95 transition-[width] duration-200",
          drawerOpen ? panelWidth : "w-0 overflow-hidden border-s-0",
        )}
      >
        {drawerOpen && (
          <>
            <div className="flex items-center justify-between border-b px-3 py-2">
              <div className="min-w-0">
                <div className="truncate text-xs font-semibold">Context</div>
                <div className="truncate text-[10px] text-muted-foreground">{sectionSubtitle}</div>
              </div>
              <button
                type="button"
                onClick={() => setDrawerOpen(false)}
                className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                title="Collapse panel"
              >
                <PanelRightClose className="h-4 w-4" />
              </button>
            </div>

            <nav className="flex border-b text-[11px]">
              {SECTIONS.map((section) => (
                <button
                  key={section.id}
                  type="button"
                  onClick={() => setActiveSection(section.id)}
                  className={cn(
                    "flex-1 px-2 py-2 font-medium transition",
                    activeSection === section.id
                      ? "border-b-2 border-primary text-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {section.label}
                  {section.id === "sources" && sourceCount > 0 && (
                    <span className="ml-1 text-[10px] text-muted-foreground">({sourceCount})</span>
                  )}
                </button>
              ))}
            </nav>

            <div className="flex-1 overflow-y-auto p-3">
              {activeSection === "sources" && <SourcesPanel />}

              {activeSection === "research" && (
                <div className="space-y-3">
                  {!ticker && (
                    <p className="text-[11px] text-muted-foreground">
                      Ask about a symbol to load hub research.
                    </p>
                  )}
                  {ticker && loadingPlan && !localPlan && (
                    <p className="text-[11px] text-muted-foreground">Loading hub research…</p>
                  )}
                  {ticker && localPlan && (
                    <ResearchContextPanel underlying={localPlan.underlying || ticker} artifact={localPlan} />
                  )}
                  {ticker && !loadingPlan && !localPlan && (
                    <p className="text-[11px] text-muted-foreground">
                      No hub plan yet — ask about strategies and the agent will generate one.
                    </p>
                  )}
                </div>
              )}

              {activeSection === "debate" && (
                <>
                  {!ticker && (
                    <p className="text-[11px] text-muted-foreground">
                      Ask about a symbol to run agent debate.
                    </p>
                  )}
                  {ticker && (
                    <AgentDebatePanel
                      ticker={ticker}
                      debate={debateArtifact ?? null}
                      running={debateRunning}
                      error={debateError}
                      onRunDebate={runDebate}
                    />
                  )}
                </>
              )}
            </div>
          </>
        )}
      </aside>
    </>
  );
}

export function SourceCitationLink({ refId, label }: { refId: string; label: string }) {
  const focusSource = useProvenanceStore((s) => s.focusSource);
  return (
    <button
      type="button"
      onClick={() => focusSource(refId)}
      className="inline-flex items-center gap-0.5 text-primary underline decoration-primary/40 underline-offset-2 hover:decoration-primary"
      title={`View source: ${label}`}
    >
      <Link2 className="h-3 w-3 shrink-0" />
      <span>{label}</span>
    </button>
  );
}
