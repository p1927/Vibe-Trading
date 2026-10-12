import { useState } from "react";
import { ForecastReplayPanel } from "@/components/prediction/forecast-replay";
import { cn } from "@/lib/utils";

interface Props {
  horizonDays: number;
}

type ViewMode = "single" | "compare";

const DEFAULT_TRACKS = ["track:quant_ridge@backtest", "track:macro_only@backtest"];

/** Backtest replay of the scoreboard tracks: the shared replay panel with backtest provenance on. */
export function TrackScoreboardReplaySection({ horizonDays }: Props) {
  const [viewMode, setViewMode] = useState<ViewMode>("single");
  return (
    <div className="space-y-4 rounded-xl border bg-card p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold">Track forecast replay vs Nifty 50</p>
          <p className="text-[10px] text-muted-foreground">
            Walk-forward eval forecasts per track (dotted = backtest) against actual Nifty (green); scroll/wheel to zoom.
          </p>
        </div>
        <div className="flex rounded-lg border border-border/60 p-0.5 text-[10px]">
          {(["single", "compare"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setViewMode(m)}
              className={cn("rounded-md px-2.5 py-1 font-medium", viewMode === m ? "bg-primary text-primary-foreground" : "text-muted-foreground")}
            >
              {m === "single" ? "Single track" : "Compare"}
            </button>
          ))}
        </div>
      </div>
      <ForecastReplayPanel
        horizon={horizonDays}
        defaultSources={DEFAULT_TRACKS}
        defaultProvenance={["backtest"]}
        single={viewMode === "single"}
        modes={["day", "week"]}
        height={viewMode === "single" ? 400 : 420}
        emptyHint={`No ${horizonDays}d backtest forecast on this day for the selected track(s). Use Prev/Next forecast to step through OOS eval dates (~every 5 sessions).`}
      />
    </div>
  );
}
