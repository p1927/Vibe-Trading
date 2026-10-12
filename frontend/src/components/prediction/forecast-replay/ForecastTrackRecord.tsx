import { useEffect, useState } from "react";
import { defaultHorizon, horizonOptions } from "@/lib/forecastReplay/horizon";
import { ErrorStrip } from "./ErrorStrip";
import { HorizonSelect } from "./HorizonSelect";
import { SourceComparisonTable } from "./SourceComparisonTable";
import { SourcePicker } from "./SourcePicker";
import { useReplayView } from "./useReplayView";

const DEFAULT_SOURCES = ["headline", "fan", "track:quant_ridge", "track:scenario_anchor"];

/**
 * Cross-day deviation view: error over time per source and the server's per-source accuracy table. The horizon
 * is the user's choice among the horizons the loaded records carry (the page horizon when present, else the
 * nearest); it is also the `horizon` the server computes its stats at.
 */
export function ForecastTrackRecord({ ticker = "NIFTY", horizon: pageHorizon }: { ticker?: string; horizon: number }) {
  const [chosen, setChosen] = useState<number | null>(null);
  const horizon = chosen ?? pageHorizon;
  const v = useReplayView({ ticker, horizon, defaultSources: DEFAULT_SOURCES, defaultProvenance: ["live"], windowDays: 180 });
  const options = horizonOptions(v.loaded);

  useEffect(() => {
    if (options.length && !options.includes(horizon)) setChosen(defaultHorizon(options, pageHorizon));
  }, [options.join(","), horizon, pageHorizon]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="space-y-3 rounded-xl border bg-card p-4 shadow-sm" data-testid="forecast-track-record">
      <SourcePicker
        sources={v.allSources} selected={v.selected} provenance={v.provenance} styles={v.styles}
        onToggleSource={v.toggleSource} onToggleProvenance={v.toggleProvenance}
      />
      {options.length ? <HorizonSelect options={options} value={horizon} onChange={setChosen} /> : null}
      {v.error ? <p className="text-[11px] text-red-600 dark:text-red-400">Forecast replay failed: {v.error}</p> : null}
      {v.bundle ? (
        <div className={v.loading ? "space-y-3 opacity-60" : "space-y-3"}>
          <ErrorStrip records={v.records} horizon={horizon} ids={v.selected} styles={v.styles} />
          <SourceComparisonTable ids={v.selected} stats={v.bundle.stats} styles={v.styles} records={v.loaded} horizon={horizon} />
        </div>
      ) : v.loading ? (
        <p className="text-[11px] text-muted-foreground">Loading forecast replay…</p>
      ) : null}
    </div>
  );
}
