import * as echarts from "echarts/core";
import type { EChartsInitOpts, EChartsType } from "echarts/core";
import { CandlestickChart, LineChart, BarChart, HeatmapChart, ScatterChart, PieChart, TreemapChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkPointComponent,
  ToolboxComponent,
  MarkLineComponent,
  MarkAreaComponent,
  VisualMapComponent,
  TitleComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  CandlestickChart, LineChart, BarChart, HeatmapChart, ScatterChart, PieChart, TreemapChart,
  GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent, MarkPointComponent,
  ToolboxComponent, MarkLineComponent, MarkAreaComponent,
  VisualMapComponent, TitleComponent,
  CanvasRenderer,
]);

export const CHART_GROUP = "quant-charts";

let _connected = false;

export function connectCharts() {
  if (!_connected) {
    echarts.connect(CHART_GROUP);
    _connected = true;
  }
}

/**
 * `echarts.init`, but safe to call before the container has been laid out
 * (a fresh tab, a flex/grid child on first paint). Measuring a 0x0 container
 * makes echarts log "can't get DOM width or height" — passing an explicit
 * fallback size skips that measurement. Every call site already tracks the
 * container with a ResizeObserver that calls `chart.resize()`, so this only
 * affects the first frame; the chart snaps to its real size as soon as
 * layout settles.
 */
export function initEChart(
  dom: HTMLElement,
  theme?: string | object | null,
  opts?: EChartsInitOpts,
): EChartsType {
  if (opts?.width === undefined && opts?.height === undefined) {
    const { clientWidth: w, clientHeight: h } = dom;
    if (w === 0 || h === 0) {
      opts = { ...opts, width: w || 300, height: h || 150 };
    }
  }
  return echarts.init(dom, theme, opts);
}

export { echarts };
