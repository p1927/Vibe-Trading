import { useEffect, useMemo, useRef } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { IndexSimulationResult } from "@/lib/api";
import { factorLabel } from "@/lib/factorEventMapping";

export interface ShockPoint {
  factor_delta_pct?: number;
  index_level?: number;
  return_pct?: number;
}

interface Props {
  label: string;
  points: ShockPoint[];
  activeShockPct: number;
  simulation?: IndexSimulationResult | null;
  height?: number;
}

function fmtLevel(v: number): string {
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function fmtPct(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function pointAtShock(points: ShockPoint[], shockPct: number): ShockPoint | null {
  if (!points.length) return null;
  const exact = points.find((p) => p.factor_delta_pct === shockPct);
  if (exact) return exact;
  const sorted = [...points].sort(
    (a, b) => Number(a.factor_delta_pct ?? 0) - Number(b.factor_delta_pct ?? 0),
  );
  for (let i = 0; i < sorted.length - 1; i += 1) {
    const lo = sorted[i];
    const hi = sorted[i + 1];
    const x0 = Number(lo.factor_delta_pct ?? 0);
    const x1 = Number(hi.factor_delta_pct ?? 0);
    if (shockPct >= x0 && shockPct <= x1 && x1 !== x0) {
      const t = (shockPct - x0) / (x1 - x0);
      return {
        factor_delta_pct: shockPct,
        index_level:
          Number(lo.index_level ?? 0) + t * (Number(hi.index_level ?? 0) - Number(lo.index_level ?? 0)),
        return_pct:
          Number(lo.return_pct ?? 0) + t * (Number(hi.return_pct ?? 0) - Number(lo.return_pct ?? 0)),
      };
    }
  }
  return sorted[0] ?? null;
}

export function FactorShockImpactChart({
  label,
  points,
  activeShockPct,
  simulation,
  height = 260,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();

  const baseline = useMemo(
    () => points.find((p) => p.factor_delta_pct === 0) ?? pointAtShock(points, 0),
    [points],
  );
  const isolatedAtShock = useMemo(
    () => pointAtShock(points, activeShockPct),
    [points, activeShockPct],
  );

  const option = useMemo(() => {
    const t = getChartTheme();
    if (points.length < 2) return null;

    const xs = points.map((p) => `${p.factor_delta_pct ?? 0}%`);
    const ys = points.map((p) => Number(p.index_level ?? 0));
    const baselineLevel = Number(baseline?.index_level ?? ys[Math.floor(ys.length / 2)] ?? 0);

    const markPointData: Array<{
      name: string;
      coord: [string, number];
      itemStyle?: { color: string };
    }> = [];

    if (isolatedAtShock?.index_level != null) {
      markPointData.push({
        name: "Isolated",
        coord: [`${activeShockPct}%`, Number(isolatedAtShock.index_level)],
        itemStyle: { color: t.infoColor },
      });
    }

    if (simulation?.index_level != null && activeShockPct !== 0) {
      markPointData.push({
        name: "Cascade",
        coord: [`${activeShockPct}%`, Number(simulation.index_level)],
        itemStyle: { color: t.upColor },
      });
    }

    return {
      backgroundColor: "transparent",
      title: {
        text: `Nifty 50 at horizon — ${label} shock`,
        left: 0,
        textStyle: { fontSize: 11, color: t.textColor, fontWeight: 600 },
      },
      legend: {
        data: ["Isolated model", ...(simulation?.index_level != null && activeShockPct !== 0 ? ["Cascade"] : [])],
        top: 4,
        right: 0,
        textStyle: { fontSize: 9, color: t.textColor },
      },
      grid: { left: 56, right: 12, top: 40, bottom: 36 },
      tooltip: {
        trigger: "axis",
        formatter: (params: unknown) => {
          const items = Array.isArray(params) ? params : [params];
          const idx = (items[0] as { dataIndex?: number })?.dataIndex ?? 0;
          const pt = points[idx];
          if (!pt) return "";
          return [
            `${label} shock: ${pt.factor_delta_pct ?? 0}%`,
            `Nifty: ${fmtLevel(Number(pt.index_level ?? 0))}`,
            `Return: ${fmtPct(Number(pt.return_pct ?? 0))}`,
          ].join("<br/>");
        },
      },
      xAxis: {
        type: "category",
        data: xs,
        name: `${label} change`,
        nameLocation: "middle",
        nameGap: 22,
        axisLabel: { fontSize: 10, color: t.textColor },
      },
      yAxis: {
        type: "value",
        scale: true,
        name: "Nifty level",
        nameTextStyle: { fontSize: 9, color: t.textColor },
        axisLabel: {
          fontSize: 10,
          color: t.textColor,
          formatter: (v: number) => fmtLevel(v),
        },
      },
      series: [
        {
          name: "Isolated model",
          type: "line",
          data: ys,
          smooth: true,
          symbol: "circle",
          symbolSize: 4,
          lineStyle: { color: t.infoColor, width: 2 },
          itemStyle: { color: t.infoColor },
          markLine: baselineLevel
            ? {
                silent: true,
                symbol: "none",
                lineStyle: { type: "dashed", color: t.textColor },
                data: [{ yAxis: baselineLevel, name: "Baseline forecast" }],
              }
            : undefined,
          markPoint: markPointData.length
            ? {
                symbol: "pin",
                symbolSize: 42,
                data: markPointData,
                label: { fontSize: 8 },
              }
            : undefined,
        },
      ],
    };
  }, [points, label, baseline, isolatedAtShock, activeShockPct, simulation, dark]);

  useEffect(() => {
    if (!ref.current || !option) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option);
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [option, dark]);

  if (points.length < 2) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-dashed text-[12px] text-muted-foreground"
        style={{ height }}
      >
        No sensitivity data — re-run index analysis.
      </div>
    );
  }

  return (
    <div
      ref={ref}
      style={{ height }}
      className="rounded-lg overflow-hidden border border-border/40 bg-muted/10"
    />
  );
}

export function ShockSummaryCards({
  baseline,
  isolated,
  simulation,
  shockPct,
}: {
  baseline: ShockPoint | null;
  isolated: ShockPoint | null;
  simulation: IndexSimulationResult | null;
  shockPct: number;
}) {
  const baseLevel = baseline?.index_level;
  const baseRet = baseline?.return_pct;

  return (
    <div className="grid gap-2 sm:grid-cols-3">
      <div className="rounded-lg bg-muted/40 px-3 py-2">
        <p className="text-[9px] uppercase text-muted-foreground">Baseline forecast</p>
        <p className="text-sm font-semibold tabular-nums">{baseLevel != null ? fmtLevel(baseLevel) : "—"}</p>
        <p className="text-[10px] text-muted-foreground">{baseRet != null ? fmtPct(baseRet) : "—"}</p>
      </div>
      <div className="rounded-lg bg-muted/40 px-3 py-2">
        <p className="text-[9px] uppercase text-muted-foreground">
          Isolated @ {shockPct > 0 ? "+" : ""}
          {shockPct}%
        </p>
        <p className="text-sm font-semibold tabular-nums">
          {isolated?.index_level != null ? fmtLevel(isolated.index_level) : "—"}
        </p>
        <p className="text-[10px] text-muted-foreground">
          {isolated?.return_pct != null ? fmtPct(isolated.return_pct) : "—"}
          {isolated?.return_pct != null && baseRet != null ? (
            <span className="ml-1">
              ({fmtPct(isolated.return_pct - baseRet)} vs base)
            </span>
          ) : null}
        </p>
      </div>
      <div
        className={[
          "rounded-lg px-3 py-2",
          shockPct !== 0 && simulation ? "bg-primary/10 ring-1 ring-primary/30" : "bg-muted/40",
        ].join(" ")}
      >
        <p className="text-[9px] uppercase text-muted-foreground">
          Cascade @ {shockPct > 0 ? "+" : ""}
          {shockPct}%
        </p>
        <p className="text-sm font-semibold tabular-nums">
          {simulation?.index_level != null ? fmtLevel(simulation.index_level) : shockPct === 0 ? fmtLevel(baseLevel ?? 0) : "…"}
        </p>
        <p className="text-[10px] text-muted-foreground">
          {simulation?.expected_return_pct != null
            ? fmtPct(simulation.expected_return_pct)
            : shockPct === 0 && baseRet != null
              ? fmtPct(baseRet)
              : "—"}
          {simulation?.expected_return_pct != null && baseRet != null ? (
            <span className="ml-1">
              ({fmtPct(simulation.expected_return_pct - baseRet)} vs base)
            </span>
          ) : null}
        </p>
      </div>
    </div>
  );
}

export function CascadeFactorBars({
  rows,
}: {
  rows: Array<{ factor?: string; before?: number; after?: number; reason?: string }>;
}) {
  const linked = rows.filter((r) => r.factor && r.reason?.includes("cascade"));
  if (!linked.length) return null;

  return (
    <div className="rounded-lg border px-3 py-2">
      <p className="text-[9px] font-semibold uppercase text-muted-foreground">
        Linked factors moved (cause → knock-on)
      </p>
      <ul className="mt-2 space-y-2">
        {linked.map((row) => {
          const before = Number(row.before ?? 0);
          const after = Number(row.after ?? 0);
          const pct = before !== 0 ? ((after - before) / Math.abs(before)) * 100 : 0;
          const width = Math.min(100, Math.abs(pct) * 4);
          return (
            <li key={row.factor} className="space-y-0.5">
              <div className="flex items-center justify-between text-[10px]">
                <span className="font-medium">{factorLabel(row.factor || "")}</span>
                <span className="tabular-nums text-muted-foreground">
                  {row.before} → {row.after}
                  {Number.isFinite(pct) ? ` (${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%)` : ""}
                </span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                <div
                  className={["h-full rounded-full", pct >= 0 ? "bg-emerald-500" : "bg-red-500"].join(" ")}
                  style={{ width: `${width}%` }}
                />
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export { pointAtShock };
