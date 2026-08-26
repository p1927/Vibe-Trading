import { useRef } from "react";
import i18n from "@/i18n";
import type { PopOverlayRow } from "@/lib/options";
import { getChartTheme } from "@/lib/chart-theme";
import { getPnlColors } from "@/lib/pnl-colors";
import { useChartLifecycle } from "@/hooks/useChartLifecycle";

interface Props {
  rows: PopOverlayRow[];
  underlyingLtp: number | null;
  height?: number;
}

/**
 * Per-strike probability-of-profit overlay: one line per option side (CE/PE)
 * plotting `probability_of_profit` against strike, with a marker line at the
 * live spot. This is the "probability distribution overlaid on the option
 * chain" the original request asked for — module 4's `compute_chain_pop_overlay`
 * scores every strike off one shared GBM path ensemble for the chosen horizon.
 */
export function PopOverlayChart({ rows, underlyingLtp, height = 320 }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useChartLifecycle(
    ref,
    () => {
      const t = getChartTheme();
      const pnlC = getPnlColors();

      const callLabel = i18n.t("options.india.popOverlay.calls", { defaultValue: "Calls (CE)" });
      const putLabel = i18n.t("options.india.popOverlay.puts", { defaultValue: "Puts (PE)" });
      const spotLabel = i18n.t("options.payoff.entrySpot", { defaultValue: "Spot" });

      const scored = rows.filter((r) => r.pop != null);
      const calls = scored
        .filter((r) => r.option_type === "CE")
        .sort((a, b) => a.strike - b.strike)
        .map((r) => [r.strike, (r.pop!.probability_of_profit * 100).toFixed(2)]);
      const puts = scored
        .filter((r) => r.option_type === "PE")
        .sort((a, b) => a.strike - b.strike)
        .map((r) => [r.strike, (r.pop!.probability_of_profit * 100).toFixed(2)]);

      return {
        backgroundColor: "transparent",
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "cross" },
          backgroundColor: t.tooltipBg,
          borderColor: t.tooltipBorder,
          textStyle: { color: t.tooltipText, fontSize: 11 },
        },
        legend: {
          data: [callLabel, putLabel],
          textStyle: { color: t.textColor, fontSize: 11 },
          right: 60,
          top: 4,
        },
        toolbox: {
          feature: { saveAsImage: { title: "Save" }, restore: { title: "Reset" } },
          right: 8,
          top: 0,
          iconStyle: { borderColor: t.textColor },
        },
        grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
        xAxis: {
          type: "value",
          min: "dataMin",
          max: "dataMax",
          name: i18n.t("options.chain.strike", { defaultValue: "Strike" }),
          nameTextStyle: { color: t.textColor, fontSize: 10 },
          nameLocation: "middle",
          nameGap: 26,
          axisLine: { lineStyle: { color: t.axisColor } },
          axisLabel: { color: t.textColor, fontSize: 10, formatter: (v: number) => v.toLocaleString() },
          splitLine: { show: false },
        },
        yAxis: {
          type: "value",
          min: 0,
          max: 100,
          name: i18n.t("options.india.pop", { defaultValue: "PoP" }) + " %",
          nameTextStyle: { color: t.textColor, fontSize: 10 },
          splitLine: { lineStyle: { color: t.gridColor } },
          axisLabel: { color: t.textColor, fontSize: 10, formatter: (v: number) => `${v}%` },
        },
        series: [
          {
            name: callLabel,
            type: "line",
            data: calls,
            symbol: "circle",
            symbolSize: 5,
            lineStyle: { color: pnlC.profit, width: 2 },
            itemStyle: { color: pnlC.profit },
            markLine: underlyingLtp
              ? {
                  silent: true,
                  symbol: "none",
                  label: { formatter: spotLabel, color: t.textColor, fontSize: 10 },
                  lineStyle: { color: t.axisColor, type: "dashed" },
                  data: [{ xAxis: underlyingLtp }],
                }
              : undefined,
          },
          {
            name: putLabel,
            type: "line",
            data: puts,
            symbol: "circle",
            symbolSize: 5,
            lineStyle: { color: pnlC.loss, width: 2 },
            itemStyle: { color: pnlC.loss },
          },
        ],
      };
    },
    [rows, underlyingLtp],
  );

  if (rows.length === 0) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        {i18n.t("options.india.popOverlay.noData", { defaultValue: "No overlay data." })}
      </div>
    );
  }
  return <div ref={ref} style={{ height }} />;
}
