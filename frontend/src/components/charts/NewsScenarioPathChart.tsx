import { useMemo } from "react";
import {
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useDarkMode } from "@/hooks/useDarkMode";
import { getChartTheme } from "@/lib/chart-theme";
import type { NewsScenarioFanBand, NewsScenarioOutcome, NewsScenarioPathPoint } from "@/lib/api";

const OUTCOME_COLORS = ["#3b82f6", "#8b5cf6", "#f59e0b", "#14b8a6", "#ec4899", "#6366f1"];

function fmtLevel(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function pathKey(row: NewsScenarioPathPoint, index: number): string {
  if (row.date) return row.date;
  if (row.day != null) return `day:${row.day}`;
  return `idx:${index}`;
}

function xLabel(key: string): string {
  if (key.startsWith("day:")) return `D${key.slice(4)}`;
  if (/^\d{4}-\d{2}-\d{2}$/.test(key)) {
    const d = new Date(`${key}T00:00:00`);
    return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
  }
  return key;
}

function spotAt(path: NewsScenarioPathPoint[] | undefined, key: string): number | null {
  if (!path?.length) return null;
  const idx = path.findIndex((row, i) => pathKey(row, i) === key);
  if (idx < 0) return null;
  const spot = path[idx]?.spot;
  return spot != null && Number.isFinite(Number(spot)) ? Number(spot) : null;
}

export interface NewsScenarioPathChartProps {
  baselinePath?: NewsScenarioPathPoint[];
  outcomes?: NewsScenarioOutcome[];
  fanBand?: NewsScenarioFanBand | null;
  selectedOutcomeId?: string | null;
  height?: number;
  compact?: boolean;
  showLegend?: boolean;
}

export function NewsScenarioPathChart({
  baselinePath = [],
  outcomes = [],
  fanBand,
  selectedOutcomeId,
  height = 280,
  compact = false,
  showLegend = true,
}: NewsScenarioPathChartProps) {
  const { dark } = useDarkMode();
  const theme = getChartTheme();

  const { chartData, seriesKeys } = useMemo(() => {
    const keys: string[] = [];
    const seen = new Set<string>();
    const addKeys = (path?: NewsScenarioPathPoint[]) => {
      (path ?? []).forEach((row, i) => {
        const key = pathKey(row, i);
        if (!seen.has(key)) {
          seen.add(key);
          keys.push(key);
        }
      });
    };
    addKeys(baselinePath);
    outcomes.forEach((o) => addKeys(o.path));

    const rows = keys.map((key) => {
      const row: Record<string, string | number | null> = { xKey: key, xLabel: xLabel(key) };
      row.baseline = spotAt(baselinePath, key);
      outcomes.forEach((outcome) => {
        const id = outcome.id || outcome.label || "outcome";
        row[`outcome_${id}`] = spotAt(outcome.path, key);
      });
      return row;
    });

    const seriesKeys = outcomes.map((o, i) => ({
      id: o.id || o.label || `outcome_${i}`,
      label: o.label || o.id || `Outcome ${i + 1}`,
      color: OUTCOME_COLORS[i % OUTCOME_COLORS.length],
      active: !selectedOutcomeId || o.id === selectedOutcomeId,
    }));

    return { chartData: rows, seriesKeys };
  }, [baselinePath, outcomes, fanBand, selectedOutcomeId]);

  const hasFanBand =
    fanBand?.low != null &&
    fanBand?.high != null &&
    Number.isFinite(Number(fanBand.low)) &&
    Number.isFinite(Number(fanBand.high));

  if (chartData.length < 2) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border border-dashed border-border/60 bg-muted/10 text-[11px] text-muted-foreground"
        style={{ height }}
      >
        Run a news scenario to see baseline vs outcome paths.
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-border/50 bg-card/30 p-2" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={chartData} margin={{ top: compact ? 4 : 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={theme.gridColor} strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="xLabel"
            tick={{ fontSize: compact ? 9 : 10, fill: theme.textColor }}
            axisLine={{ stroke: theme.axisColor }}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            domain={["auto", "auto"]}
            tick={{ fontSize: compact ? 9 : 10, fill: theme.textColor }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v) => fmtLevel(Number(v))}
            width={compact ? 44 : 52}
          />
          <Tooltip
            contentStyle={{
              background: theme.tooltipBg,
              border: `1px solid ${theme.tooltipBorder}`,
              borderRadius: 8,
              fontSize: 11,
              color: theme.tooltipText,
            }}
            formatter={(value, name) => {
              const n = Number(value);
              if (!Number.isFinite(n)) return ["—", String(name)];
              if (name === "fanLow" || name === "fanHigh") return [fmtLevel(n), String(name)];
              return [fmtLevel(n), String(name)];
            }}
            labelFormatter={(label) => String(label)}
          />
          {hasFanBand ? (
            <ReferenceArea
              y1={Number(fanBand!.low)}
              y2={Number(fanBand!.high)}
              fill={theme.infoColor}
              fillOpacity={dark ? 0.1 : 0.07}
              strokeOpacity={0}
            />
          ) : null}
          <Line
            type="monotone"
            dataKey="baseline"
            name="Baseline"
            stroke={theme.textColor}
            strokeWidth={compact ? 1.5 : 2}
            strokeDasharray="6 4"
            dot={compact ? false : { r: 2 }}
            connectNulls
            isAnimationActive={false}
          />
          {seriesKeys.map((s) => (
            <Line
              key={s.id}
              type="monotone"
              dataKey={`outcome_${s.id}`}
              name={s.label}
              stroke={s.color}
              strokeWidth={s.active ? (compact ? 2 : 2.5) : 1.25}
              strokeOpacity={s.active ? 1 : 0.35}
              dot={compact ? false : { r: s.active ? 3 : 2 }}
              connectNulls
              isAnimationActive={false}
            />
          ))}
          {showLegend && !compact ? (
            <Legend
              verticalAlign="top"
              align="right"
              iconType="plainline"
              wrapperStyle={{ fontSize: 10, color: theme.textColor, paddingBottom: 4 }}
            />
          ) : null}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

export function formatOutcomeReturn(outcome: NewsScenarioOutcome): string {
  return fmtPct(outcome.expected_return_pct);
}
