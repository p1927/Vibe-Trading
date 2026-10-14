import { useRef } from "react";
import i18n from "@/i18n";
import type { AttributionBrinson } from "@/lib/api";
import { getChartTheme } from "@/lib/chart-theme";
import { getPnlColors } from "@/lib/pnl-colors";
import { useChartLifecycle } from "@/hooks/useChartLifecycle";

export interface WaterfallBar {
  base: number;
  value: number;
  kind: "level" | "effect";
}

// Waterfall identity: benchmark + allocation + selection + interaction equals
// portfolio, so each effect bar starts where the previous bar ended.
export function buildWaterfallBars(
  brinson: Pick<AttributionBrinson, "benchmark_return" | "allocation" | "selection" | "interaction">,
): WaterfallBar[] {
  const b = brinson.benchmark_return;
  const a = brinson.allocation;
  const s = brinson.selection;
  const x = brinson.interaction;
  return [
    { base: 0, value: b, kind: "level" },
    { base: b, value: a, kind: "effect" },
    { base: b + a, value: s, kind: "effect" },
    { base: b + a + s, value: x, kind: "effect" },
    { base: 0, value: b + a + s + x, kind: "level" },
  ];
}

function fmtSignedPct(v: number): string {
  return `${v > 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;
}

export interface WaterfallStack {
  /** Invisible spacer from zero to the bar's near edge (signed), */
  placeholder: number;
  /** then the part of the bar above zero (>= 0) and the part below zero (<= 0). */
  pos: number;
  neg: number;
}

// ECharts stacks positive and negative values on separate sides of zero, so a floating bar needs its
// spacer on the same side as the bar, and a bar that crosses zero needs one part on each side. A single
// `min(base, base + value)` spacer with an `|value|` bar draws every bar below zero upward.
export function waterfallStack(bar: WaterfallBar): WaterfallStack {
  const lo = Math.min(bar.base, bar.base + bar.value);
  const hi = Math.max(bar.base, bar.base + bar.value);
  if (lo >= 0) return { placeholder: lo, pos: hi - lo, neg: 0 };
  if (hi <= 0) return { placeholder: hi, pos: 0, neg: lo - hi };
  return { placeholder: 0, pos: hi, neg: lo };
}

interface Props {
  brinson: AttributionBrinson;
  height?: number;
}

export function AttributionWaterfallChart({ brinson, height = 300 }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useChartLifecycle(ref, () => {
    const t = getChartTheme();
    const pnl = getPnlColors();

    const bars = buildWaterfallBars(brinson);
    const categories = [
      i18n.t("runDetail.attrBenchmark"),
      i18n.t("runDetail.attrAllocation"),
      i18n.t("runDetail.attrSelection"),
      i18n.t("runDetail.attrInteraction"),
      i18n.t("runDetail.attrPortfolio"),
    ];
    const valueLabel = i18n.t("runDetail.returnPct");

    const stacks = bars.map(waterfallStack);
    const pct4 = (v: number) => +(v * 100).toFixed(4);
    const placeholders = stacks.map((st) => pct4(st.placeholder));
    const part = (pick: "pos" | "neg") =>
      bars.map((bar, i) => ({
        value: pct4(stacks[i][pick]),
        itemStyle: {
          color: bar.kind === "level" ? t.infoColor : bar.value >= 0 ? pnl.profit : pnl.loss,
          borderRadius: 2,
        },
        // one label per bar, on the part that carries it (the above-zero part unless the bar is all below)
        label: {
          show: pick === (stacks[i].pos !== 0 || stacks[i].neg === 0 ? "pos" : "neg"),
          position: bar.value >= 0 ? "top" : "bottom",
          formatter: fmtSignedPct(bar.value),
          fontSize: 10,
          color: bar.kind === "level" ? t.textColor : bar.value >= 0 ? pnl.profit : pnl.loss,
        },
      }));

    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        backgroundColor: t.tooltipBg,
        borderColor: t.tooltipBorder,
        textStyle: { color: t.tooltipText, fontSize: 11 },
        formatter: (raw: unknown) => {
          const params = raw as Array<{ dataIndex?: number }>;
          if (!Array.isArray(params) || !params.length) return "";
          const idx = params[0].dataIndex ?? 0;
          const bar = bars[idx];
          if (!bar) return "";
          return `<b>${categories[idx]}</b><br/>${valueLabel}: <b>${fmtSignedPct(bar.value)}</b>`;
        },
      },
      grid: { left: 8, right: 8, top: 28, bottom: 8, containLabel: true },
      xAxis: {
        type: "category",
        data: categories,
        axisLine: { lineStyle: { color: t.axisColor } },
        axisLabel: { color: t.textColor, fontSize: 10 },
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { color: t.gridColor } },
        axisLabel: { color: t.textColor, fontSize: 10, formatter: "{value}%" },
      },
      series: [
        {
          name: "placeholder",
          type: "bar",
          stack: "waterfall",
          data: placeholders,
          silent: true,
          itemStyle: { color: "transparent" },
          emphasis: { itemStyle: { color: "transparent" } },
          tooltip: { show: false },
        },
        { name: valueLabel, type: "bar", stack: "waterfall", data: part("pos"), barWidth: "45%" },
        { name: `${valueLabel} (below 0)`, type: "bar", stack: "waterfall", data: part("neg"), barWidth: "45%", tooltip: { show: false } },
      ],
    };
  }, [brinson]);

  return <div ref={ref} style={{ height }} />;
}
