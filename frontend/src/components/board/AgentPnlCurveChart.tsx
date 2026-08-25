import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getChartTheme } from "@/lib/chart-theme";

const SERIES_COLORS = ["#3b82f6", "#8b5cf6", "#f59e0b", "#14b8a6", "#ec4899", "#6366f1"];

function fmtInr(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
}

function fmtDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

export interface PnlSeries {
  key: string;
  label: string;
  color?: string;
  points: Array<{ at: string; value: number }>;
}

export interface AgentPnlCurveChartProps {
  series: PnlSeries[];
  height?: number;
  emptyLabel?: string;
}

/** Generic multi-series cumulative-P&L line chart — one x-axis timeline (each series'
 * own `at`/`value` points merged by exit timestamp), used for both the agent-actual vs.
 * shadow-track wealth curve (2 series) and the multi-candidate hindsight comparison
 * (one series per candidate rank) — see 2026-08-25-dual-board-advisory-agent-ui. */
export function AgentPnlCurveChart({ series, height = 260, emptyLabel }: AgentPnlCurveChartProps) {
  const theme = getChartTheme();
  const nonEmpty = series.filter((s) => s.points.length > 0);

  if (nonEmpty.length === 0) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border border-dashed border-border/60 bg-muted/10 text-[11px] text-muted-foreground"
        style={{ height }}
      >
        {emptyLabel ?? "No closed trades yet."}
      </div>
    );
  }

  const allKeys = Array.from(new Set(nonEmpty.flatMap((s) => s.points.map((p) => p.at)))).sort();
  const chartData = allKeys.map((at) => {
    const row: Record<string, string | number | null> = { at, xLabel: fmtDate(at) };
    nonEmpty.forEach((s) => {
      const point = [...s.points].reverse().find((p) => p.at <= at);
      row[s.key] = point ? point.value : null;
    });
    return row;
  });

  return (
    <div className="rounded-xl border border-border/50 bg-card/30 p-2" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={theme.gridColor} strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="xLabel"
            tick={{ fontSize: 10, fill: theme.textColor }}
            axisLine={{ stroke: theme.axisColor }}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            domain={["auto", "auto"]}
            tick={{ fontSize: 10, fill: theme.textColor }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v) => fmtInr(Number(v))}
            width={72}
          />
          <Tooltip
            contentStyle={{
              background: theme.tooltipBg,
              border: `1px solid ${theme.tooltipBorder}`,
              borderRadius: 8,
              fontSize: 11,
              color: theme.tooltipText,
            }}
            formatter={(value, name) => [fmtInr(Number(value)), String(name)]}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {nonEmpty.map((s, i) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={s.color ?? SERIES_COLORS[i % SERIES_COLORS.length]}
              strokeWidth={2}
              dot={false}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
