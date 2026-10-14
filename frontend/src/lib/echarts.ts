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
  GraphicComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  CandlestickChart, LineChart, BarChart, HeatmapChart, ScatterChart, PieChart, TreemapChart,
  GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent, MarkPointComponent,
  ToolboxComponent, MarkLineComponent, MarkAreaComponent,
  VisualMapComponent, TitleComponent,
  GraphicComponent,
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
/**
 * ECharts injects a custom tooltip `formatter`'s string via `innerHTML`. Dozens of formatters
 * interpolate backend-derived text (series names, axis values, source names) without escaping,
 * so ONE chokepoint sanitizes them all (see lib/escapeHtml.ts for why this matters: the API key
 * lives in localStorage). Everything is HTML-escaped except the tiny markup the formatters
 * themselves emit: <b>, <br>, <span style="..."> (echarts' own colour marker is one) and already
 * escaped entities, so formatters that call escapeHtml are not double-escaped.
 */
const SAFE_TOOLTIP_TAG = /<\/?(?:b|span)>|<br\s*\/?>|<span style="[\w:;#.,()% -]*">|<\/span>/y;
const SAFE_TOOLTIP_ENTITY = /&(?:amp|lt|gt|quot|#39|nbsp);/y;

export function sanitizeTooltipHtml(html: string): string {
  let out = "";
  for (let i = 0; i < html.length; i++) {
    const ch = html[i];
    if (ch === "<" || ch === "&") {
      const re = ch === "<" ? SAFE_TOOLTIP_TAG : SAFE_TOOLTIP_ENTITY;
      re.lastIndex = i;
      const m = re.exec(html);
      if (m) {
        out += m[0];
        i += m[0].length - 1;
        continue;
      }
    }
    out += ch === "&" ? "&amp;" : ch === "<" ? "&lt;" : ch === ">" ? "&gt;" : ch === '"' ? "&quot;" : ch === "'" ? "&#39;" : ch;
  }
  return out;
}

type Tooltipish = { formatter?: unknown } & Record<string, unknown>;

function wrapFormatter(tip: unknown): void {
  if (Array.isArray(tip)) return tip.forEach(wrapFormatter);
  const t = tip as Tooltipish | null;
  if (!t || typeof t !== "object" || typeof t.formatter !== "function") return;
  const inner = t.formatter as (...a: unknown[]) => unknown;
  t.formatter = (...a: unknown[]) => {
    const r = inner(...a);
    return typeof r === "string" ? sanitizeTooltipHtml(r) : r;
  };
}

/** Wrap every tooltip formatter reachable from an option (root, series, mark components). */
export function sanitizeOptionTooltips(option: unknown): void {
  if (Array.isArray(option)) return option.forEach(sanitizeOptionTooltips);
  const o = option as Record<string, unknown> | null;
  if (!o || typeof o !== "object") return;
  wrapFormatter(o.tooltip);
  const series = Array.isArray(o.series) ? o.series : o.series ? [o.series] : [];
  for (const s of series as Array<Record<string, unknown>>) {
    if (!s || typeof s !== "object") continue;
    wrapFormatter(s.tooltip);
    for (const k of ["markPoint", "markLine", "markArea"]) {
      const m = s[k] as Record<string, unknown> | undefined;
      if (m && typeof m === "object") wrapFormatter(m.tooltip);
    }
  }
}

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
  const chart = echarts.init(dom, theme, opts);
  const setOption = chart.setOption.bind(chart) as (o: unknown, ...r: unknown[]) => void;
  chart.setOption = ((option: unknown, ...rest: unknown[]) => {
    sanitizeOptionTooltips(option);
    return setOption(option, ...rest);
  }) as typeof chart.setOption;
  return chart;
}

export { echarts };
