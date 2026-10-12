import { ErrorStrip } from "./ErrorStrip";
import { SourceComparisonTable } from "./SourceComparisonTable";
import { SourcePicker } from "./SourcePicker";
import { useReplayView } from "./useReplayView";

const DEFAULT_SOURCES = ["headline", "fan", "track:quant_ridge", "track:scenario_anchor"];

/** Cross-day deviation view: error over time per source and the server's per-source accuracy table. */
export function ForecastTrackRecord({ ticker = "NIFTY", horizon }: { ticker?: string; horizon: number }) {
  const v = useReplayView({ ticker, horizon, defaultSources: DEFAULT_SOURCES, defaultProvenance: ["live"], windowDays: 180 });
  return (
    <div className="space-y-3 rounded-xl border bg-card p-4 shadow-sm" data-testid="forecast-track-record">
      <SourcePicker
        sources={v.allSources} selected={v.selected} provenance={v.provenance} styles={v.styles}
        onToggleSource={v.toggleSource} onToggleProvenance={v.toggleProvenance}
      />
      {v.error ? <p className="text-[11px] text-red-600 dark:text-red-400">Forecast replay failed: {v.error}</p> : null}
      {v.bundle ? (
        <>
          <ErrorStrip records={v.records} horizon={horizon} ids={v.selected} styles={v.styles} />
          <SourceComparisonTable ids={v.selected} stats={v.bundle.stats} styles={v.styles} />
        </>
      ) : v.loading ? (
        <p className="text-[11px] text-muted-foreground">Loading forecast replay…</p>
      ) : null}
    </div>
  );
}
