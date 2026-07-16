import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useMemo } from "react";
import { cn } from "@/lib/utils";
import { useProvenanceStore } from "@/stores/provenance";

const CATEGORY_LABELS: Record<string, string> = {
  market_data: "Market data",
  research: "Research",
  backtest: "Backtest",
  web: "Web",
  evidence: "Evidence",
  tool: "Tool",
};

function formatMeta(provider?: string, retrievedAt?: string, freshness?: string): string {
  const parts: string[] = [];
  if (provider) parts.push(provider);
  if (freshness && freshness !== "unknown") parts.push(freshness);
  if (retrievedAt) {
    try {
      const d = new Date(retrievedAt);
      if (!Number.isNaN(d.getTime())) {
        parts.push(d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
      }
    } catch {
      /* ignore */
    }
  }
  return parts.join(" · ");
}

export function SourcesPanel() {
  const sources = useProvenanceStore((s) => s.sources);
  const expandedRefIds = useProvenanceStore((s) => s.expandedRefIds);
  const focusedRefId = useProvenanceStore((s) => s.focusedRefId);
  const toggleExpanded = useProvenanceStore((s) => s.toggleExpanded);

  useEffect(() => {
    if (!focusedRefId) return;
    const el = document.getElementById(`source-row-${focusedRefId}`);
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [focusedRefId]);

  const sorted = useMemo(
    () => [...sources].sort((a, b) => (b.retrieved_at || "").localeCompare(a.retrieved_at || "")),
    [sources],
  );

  if (sorted.length === 0) {
    return (
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        Data references will appear here as the agent uses tools, research, and market data.
      </p>
    );
  }

  return (
    <ul className="space-y-1">
      {sorted.map((source) => {
        const expanded = expandedRefIds.has(source.ref_id);
        const focused = focusedRefId === source.ref_id;
        const meta = formatMeta(source.provider, source.retrieved_at, source.freshness_status);
        const category = CATEGORY_LABELS[source.category || "tool"] || source.category;

        return (
          <li key={source.ref_id} id={`source-row-${source.ref_id}`}>
            <button
              type="button"
              onClick={() => toggleExpanded(source.ref_id)}
              className={cn(
                "flex w-full items-start gap-1.5 rounded-md px-2 py-2 text-left text-[11px] transition hover:bg-muted/40",
                focused && "bg-muted/50",
              )}
            >
              {expanded ? (
                <ChevronDown className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronRight className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" />
              )}
              <span className="min-w-0 flex-1">
                <span className="block font-medium text-foreground">{source.display_name}</span>
                <span className="block text-muted-foreground">{source.summary}</span>
                {(meta || category) && (
                  <span className="mt-0.5 block text-[10px] text-muted-foreground/70">
                    {[category, meta].filter(Boolean).join(" · ")}
                  </span>
                )}
              </span>
            </button>

            {expanded && source.raw_data && (
              <pre className="mx-2 mb-2 max-h-64 overflow-auto rounded border bg-muted/20 p-2 text-[10px] leading-relaxed text-muted-foreground whitespace-pre-wrap break-words">
                {source.raw_data}
              </pre>
            )}
          </li>
        );
      })}
    </ul>
  );
}
