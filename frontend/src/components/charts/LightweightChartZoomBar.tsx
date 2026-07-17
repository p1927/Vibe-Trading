import { Maximize2, ZoomIn, ZoomOut } from "lucide-react";
import type { IChartApi } from "lightweight-charts";
import {
  fitLightweightChart,
  zoomLightweightChartIn,
  zoomLightweightChartOut,
} from "@/lib/lightweightChartOptions";

interface Props {
  chart: IChartApi | null;
  className?: string;
}

/** Zoom controls for lightweight-charts (same engine as Nifty forecast replay). */
export function LightweightChartZoomBar({ chart, className }: Props) {
  return (
    <div
      className={`flex flex-wrap items-center justify-between gap-2 text-[10px] text-muted-foreground ${className ?? ""}`}
    >
      <span>Scroll to pan · wheel or pinch to zoom</span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          disabled={!chart}
          onClick={() => chart && zoomLightweightChartIn(chart)}
          className="inline-flex items-center gap-1 rounded border border-border/60 px-2 py-0.5 hover:bg-muted disabled:opacity-40"
          title="Zoom in"
        >
          <ZoomIn className="h-3 w-3" />
          In
        </button>
        <button
          type="button"
          disabled={!chart}
          onClick={() => chart && zoomLightweightChartOut(chart)}
          className="inline-flex items-center gap-1 rounded border border-border/60 px-2 py-0.5 hover:bg-muted disabled:opacity-40"
          title="Zoom out"
        >
          <ZoomOut className="h-3 w-3" />
          Out
        </button>
        <button
          type="button"
          disabled={!chart}
          onClick={() => chart && fitLightweightChart(chart)}
          className="inline-flex items-center gap-1 rounded border border-border/60 px-2 py-0.5 hover:bg-muted disabled:opacity-40"
          title="Fit all data"
        >
          <Maximize2 className="h-3 w-3" />
          Fit
        </button>
      </div>
    </div>
  );
}
