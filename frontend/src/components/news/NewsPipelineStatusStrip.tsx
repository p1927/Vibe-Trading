import type { HubNewsPipelineStatus } from "@/lib/api";

/**
 * Compact health strip for the Hub "Pipeline view", rendered from `hub.news_pipeline` — the
 * `hub_news_pipeline_status()` payload already on the wire from /trade/hub/status, so no extra
 * request.
 *
 * Shows only what no other part of the Hub page renders. Pause reason, migration-needed and the
 * last worker batch are NOT repeated here: the page already shows them from `news_staging` /
 * `gates` (the banners at the top and the staging card). See
 * 2026-09-07-hub-news-pipeline-status-transported-unrendered.
 */
export function NewsPipelineStatusStrip({ status }: { status?: HubNewsPipelineStatus | null }) {
  if (!status) return null;
  const wiki = status.llm_wiki;
  const health = wiki?.health;
  const aligned = wiki?.path_alignment?.aligned;

  let wikiLabel: string;
  if (!health || health.probed === false) {
    // Summary mode deliberately skips the network probe — "not checked", never "down".
    wikiLabel = "not probed (summary)";
  } else {
    wikiLabel = health.reachable ? "reachable" : "unreachable";
  }

  const items: Array<{ key: string; label: string; value: string; warn?: boolean }> = [
    { key: "wiki", label: "LLM-Wiki", value: wikiLabel, warn: health?.probed !== false && health?.reachable === false },
    {
      key: "align",
      label: "Wiki project path",
      value: aligned === undefined ? "unknown" : aligned ? "aligned" : "MISALIGNED",
      warn: aligned === false,
    },
    {
      key: "embed",
      label: "Embeddings",
      value: wiki?.embedding_available ? "available" : "unavailable",
      warn: wiki?.embedding_available === false,
    },
    { key: "sources", label: "Local source files", value: String(wiki?.local_source_md_count ?? "—") },
    { key: "events", label: "Distilled events", value: String(status.distilled_event_count ?? "—") },
    {
      key: "gate",
      label: "Relevance gate",
      value: status.relevance_gate_enabled === undefined ? "—" : status.relevance_gate_enabled ? "on" : "off",
    },
  ];

  return (
    <div
      data-testid="news-pipeline-status-strip"
      className="flex flex-wrap gap-x-4 gap-y-1 rounded-lg border bg-muted/20 px-3 py-2 text-[11px]"
    >
      {items.map((item) => (
        <span key={item.key} data-testid={`news-pipeline-status-${item.key}`}>
          <span className="text-muted-foreground">{item.label}:</span>{" "}
          <span className={item.warn ? "font-semibold text-amber-700 dark:text-amber-300" : "font-medium"}>
            {item.value}
          </span>
        </span>
      ))}
      {aligned === false && wiki?.path_alignment?.registered_path ? (
        <span className="w-full font-mono text-[10px] text-amber-700 dark:text-amber-300">
          registered {wiki.path_alignment.registered_path} ≠ expected {wiki.path_alignment.expected_path}
        </span>
      ) : null}
    </div>
  );
}
