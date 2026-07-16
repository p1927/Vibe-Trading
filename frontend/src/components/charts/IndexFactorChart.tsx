import { useEffect, useMemo, useRef, useState } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";

export interface FactorSensitivityPoint {
  factor_delta_pct?: number;
  index_level?: number;
  return_pct?: number;
}

export interface FactorSensitivityCurve {
  factor?: string;
  label?: string;
  current_value?: number;
  points?: FactorSensitivityPoint[];
}

export interface EventImpactPoint {
  shock_progress?: number;
  index_level?: number;
  return_pct?: number;
  primary_factor?: string;
}

export interface EventImpactCurve {
  event?: string;
  outcome?: string;
  probability?: number;
  index_level?: number;
  return_pct?: number;
  curve?: EventImpactPoint[];
}

interface Props {
  sensitivity: FactorSensitivityCurve[];
  eventCurves?: EventImpactCurve[];
  spot?: number;
  height?: number;
}

export function IndexFactorChart({
  sensitivity,
  eventCurves = [],
  spot,
  height = 220,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const [mode, setMode] = useState<"factor" | "event">("factor");
  const [factorIdx, setFactorIdx] = useState(0);

  const activeFactor = sensitivity[factorIdx] ?? sensitivity[0];
  const activeEvent = eventCurves[0];

  const factorOption = useMemo(() => {
    const t = getChartTheme();
    const points = activeFactor?.points ?? [];
    if (points.length < 2) return null;
    const xs = points.map((p) => Number(p.factor_delta_pct ?? 0));
    const ys = points.map((p) => Number(p.index_level ?? 0));
    const spotLine = spot && spot > 0 ? spot : null;
    return {
      backgroundColor: "transparent",
      title: {
        text: activeFactor?.label || activeFactor?.factor || "Factor",
        left: 0,
        textStyle: { fontSize: 11, color: t.textColor, fontWeight: 600 },
      },
      grid: { left: 48, right: 12, top: 32, bottom: 36 },
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "category",
        data: xs.map((x) => `${x}%`),
        name: "Factor Δ%",
        nameLocation: "middle",
        nameGap: 22,
        axisLabel: { fontSize: 10, color: t.textColor },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: { fontSize: 10, color: t.textColor },
      },
      series: [
        {
          type: "line",
          data: ys,
          smooth: true,
          symbol: "circle",
          symbolSize: 5,
          lineStyle: { color: t.infoColor, width: 2 },
          itemStyle: { color: t.infoColor },
          markLine: spotLine
            ? {
                silent: true,
                symbol: "none",
                lineStyle: { type: "dashed", color: t.textColor },
                data: [{ yAxis: spotLine, name: "Spot" }],
              }
            : undefined,
        },
      ],
    };
  }, [activeFactor, spot, dark]);

  const eventOption = useMemo(() => {
    const t = getChartTheme();
    const points = activeEvent?.curve ?? [];
    if (points.length < 2) return null;
    const xs = points.map((p) => Math.round(Number(p.shock_progress ?? 0) * 100));
    const ys = points.map((p) => Number(p.index_level ?? 0));
    const title = `${activeEvent?.event ?? "Event"} — ${activeEvent?.outcome ?? ""}`.trim();
    return {
      backgroundColor: "transparent",
      title: {
        text: title,
        left: 0,
        textStyle: { fontSize: 11, color: t.textColor, fontWeight: 600 },
      },
      grid: { left: 48, right: 12, top: 32, bottom: 36 },
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "category",
        data: xs.map((x) => `${x}%`),
        name: "Event shock",
        nameLocation: "middle",
        nameGap: 22,
        axisLabel: { fontSize: 10, color: t.textColor },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: { fontSize: 10, color: t.textColor },
      },
      series: [
        {
          type: "line",
          data: ys,
          smooth: true,
          symbol: "circle",
          symbolSize: 5,
          lineStyle: { color: t.upColor, width: 2 },
          itemStyle: { color: t.upColor },
        },
      ],
    };
  }, [activeEvent, dark]);

  useEffect(() => {
    if (!ref.current) return;
    const option = mode === "factor" ? factorOption : eventOption;
    if (!option) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option);
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [factorOption, eventOption, mode, dark]);

  if (!sensitivity.length && !eventCurves.length) return null;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setMode("factor")}
          className={[
            "rounded-md px-2 py-1 text-[10px] border",
            mode === "factor"
              ? "border-primary/60 bg-primary/10 text-foreground"
              : "border-border/60 text-muted-foreground",
          ].join(" ")}
        >
          Factor sweep
        </button>
        {eventCurves.length > 0 && (
          <button
            type="button"
            onClick={() => setMode("event")}
            className={[
              "rounded-md px-2 py-1 text-[10px] border",
              mode === "event"
                ? "border-primary/60 bg-primary/10 text-foreground"
                : "border-border/60 text-muted-foreground",
            ].join(" ")}
          >
            Event path
          </button>
        )}
        {mode === "factor" && sensitivity.length > 1 && (
          <select
            value={factorIdx}
            onChange={(e) => setFactorIdx(Number(e.target.value))}
            className="ml-auto rounded-md border border-border/60 bg-background px-2 py-1 text-[10px]"
          >
            {sensitivity.map((curve, i) => (
              <option key={curve.factor || i} value={i}>
                {curve.label || curve.factor}
              </option>
            ))}
          </select>
        )}
      </div>
      <div
        ref={ref}
        style={{ height }}
        className="rounded-lg overflow-hidden border border-border/40 bg-muted/10"
      />
    </div>
  );
}
