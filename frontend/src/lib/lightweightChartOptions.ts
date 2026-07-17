import {
  ColorType,
  CrosshairMode,
  createChart,
  type ChartOptions,
  type DeepPartial,
  type IChartApi,
} from "lightweight-charts";
import { getChartTheme } from "@/lib/chart-theme";

/** Shared TradingView lightweight-charts defaults (scroll + wheel/pinch zoom). */
export function lightweightChartBaseOptions(
  height: number,
  width?: number,
): DeepPartial<ChartOptions> {
  const t = getChartTheme();
  return {
    width,
    height,
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor: t.textColor,
    },
    grid: {
      vertLines: { color: `${t.gridColor}88`, visible: true },
      horzLines: { color: `${t.gridColor}88`, visible: true },
    },
    rightPriceScale: {
      borderColor: `${t.axisColor}55`,
      scaleMargins: { top: 0.08, bottom: 0.08 },
    },
    timeScale: {
      borderColor: `${t.axisColor}55`,
      timeVisible: true,
      secondsVisible: false,
    },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: { labelVisible: true },
      horzLine: { labelVisible: true },
    },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
  };
}

export function createLightweightChart(
  container: HTMLElement,
  height: number,
): IChartApi {
  const width = container.clientWidth;
  return createChart(container, lightweightChartBaseOptions(height, width > 0 ? width : undefined));
}

export function fitLightweightChart(chart: IChartApi): void {
  chart.timeScale().fitContent();
}

export function zoomLightweightChartTimeScale(chart: IChartApi, factor: number): void {
  const ts = chart.timeScale();
  const range = ts.getVisibleLogicalRange();
  if (!range) {
    fitLightweightChart(chart);
    return;
  }
  const center = (range.from + range.to) / 2;
  const half = Math.max(2, ((range.to - range.from) / 2) * factor);
  ts.setVisibleLogicalRange({ from: center - half, to: center + half });
}

export function zoomLightweightChartIn(chart: IChartApi): void {
  zoomLightweightChartTimeScale(chart, 0.75);
}

export function zoomLightweightChartOut(chart: IChartApi): void {
  zoomLightweightChartTimeScale(chart, 1.35);
}
