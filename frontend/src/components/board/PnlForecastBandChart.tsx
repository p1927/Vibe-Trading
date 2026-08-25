import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";

function fmtInr(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
}

export interface PnlForecastBandPoint {
  days_ahead: number;
  pnl_p10_inr: number;
  pnl_p50_inr: number;
  pnl_p90_inr: number;
}

export interface PnlForecastBandChartProps {
  band: PnlForecastBandPoint[];
  height?: number;
  emptyLabel?: string;
}

/** Day-by-day p10/p50/p90 P&L forecast for one open option position — the literal "area
 * chart of where we expect the option's value to be" ask behind
 * 2026-08-25-option-value-forecast-band-from-gbm-paths /
 * 2026-08-25-live-positions-forecast-band-board. Recharts has no native band-between-two-
 * lines primitive, so this uses the standard stacked-Area trick: an invisible base Area up
 * to p10, then a second stacked Area spanning (p90 - p10) that renders as the shaded band;
 * a Line on top traces the median (p50) path. */
export function PnlForecastBandChart({ band, height = 220, emptyLabel }: PnlForecastBandChartProps) {
  const theme = getChartTheme();
  const { dark } = useDarkMode();

  if (band.length === 0) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border border-dashed border-border/60 bg-muted/10 text-[11px] text-muted-foreground"
        style={{ height }}
      >
        {emptyLabel ?? "No forecast band available."}
      </div>
    );
  }

  const chartData = band.map((row) => ({
    xLabel: row.days_ahead === 0 ? "Now" : `D+${row.days_ahead}`,
    p10: row.pnl_p10_inr,
    band: row.pnl_p90_inr - row.pnl_p10_inr,
    p50: row.pnl_p50_inr,
    p90: row.pnl_p90_inr,
  }));

  return (
    <div className="rounded-xl border border-border/50 bg-card/30 p-2" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
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
            formatter={(value, name) => {
              if (name === "p50") return [fmtInr(Number(value)), "Median (p50)"];
              if (name === "p10") return [fmtInr(Number(value)), "p10"];
              return [fmtInr(Number(value)), String(name)];
            }}
          />
          <Area
            type="monotone"
            dataKey="p10"
            stackId="band"
            stroke="none"
            fill="transparent"
            isAnimationActive={false}
          />
          <Area
            type="monotone"
            dataKey="band"
            stackId="band"
            stroke="none"
            fill={theme.infoColor}
            fillOpacity={dark ? 0.18 : 0.12}
            isAnimationActive={false}
            name="p10–p90 range"
          />
          <Line
            type="monotone"
            dataKey="p50"
            stroke={theme.infoColor}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
            name="p50"
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
