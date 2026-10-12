import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";
import { gapPolygons } from "@/lib/forecastReplay/gap";
import type { ReplayActual, ReplayRecord } from "@/lib/forecastReplay/types";

export interface GapSpec {
  /** Records to shade, each with the fill opacity its provenance allows. */
  items: { record: ReplayRecord; alpha: number }[];
  actuals: ReplayActual[];
  above: string;
  below: string;
}

const EMPTY: GapSpec = { items: [], actuals: [], above: "#22c55e", below: "#ef4444" };

function rgba(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const n = h.length === 3 ? [...h].map((c) => c + c).join("") : h.slice(0, 6);
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(n.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/**
 * Series primitive that fills the area between each record's recorded predicted path and the actual closes
 * over its matured span (lightweight-charts has no fill between two lines). Geometry comes from the chart's
 * own time/price scales on every paint, so zoom and scroll need no extra handling.
 */
export class ForecastGapPrimitive implements ISeriesPrimitive<Time> {
  private spec: GapSpec = EMPTY;
  private param: SeriesAttachedParameter<Time> | null = null;
  private readonly view: IPrimitivePaneView = {
    zOrder: () => "bottom",
    renderer: (): IPrimitivePaneRenderer => ({
      draw: (target) => this.draw(target),
    }),
  };

  attached(param: SeriesAttachedParameter<Time>) {
    this.param = param;
  }

  detached() {
    this.param = null;
  }

  paneViews() {
    return [this.view];
  }

  setSpec(spec: GapSpec) {
    this.spec = spec;
    this.param?.requestUpdate();
  }

  private draw(target: Parameters<IPrimitivePaneRenderer["draw"]>[0]) {
    const p = this.param;
    if (!p || !this.spec.items.length) return;
    const series = p.series as ISeriesApi<"Line", Time>;
    const scale = p.chart.timeScale();
    const xOf = (d: string) => scale.timeToCoordinate(d as Time);
    target.useMediaCoordinateSpace(({ context: ctx }) => {
      for (const { record, alpha } of this.spec.items) {
        for (const poly of gapPolygons(record, this.spec.actuals, xOf)) {
          const act = poly.pts.map((s) => series.priceToCoordinate(s.act));
          const pred = poly.pts.map((s) => series.priceToCoordinate(s.pred));
          if (act.some((y) => y == null) || pred.some((y) => y == null)) continue;
          ctx.beginPath();
          poly.pts.forEach((s, i) => (i ? ctx.lineTo(s.x, act[i] as number) : ctx.moveTo(s.x, act[i] as number)));
          for (let i = poly.pts.length - 1; i >= 0; i--) ctx.lineTo(poly.pts[i].x, pred[i] as number);
          ctx.closePath();
          ctx.fillStyle = rgba(poly.sign > 0 ? this.spec.above : this.spec.below, alpha);
          ctx.fill();
        }
      }
    });
  }
}
