import { useEffect, useMemo, useRef, useState } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import { api, type ConstituentHistoryPoint } from "@/lib/api";

interface Props {
  symbol: string;
  weight?: number;
  days?: number;
}

export function ConstituentHistoryPanel({ symbol, weight, days = 90 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const [points, setPoints] = useState<ConstituentHistoryPoint[]>([]);
  const [snapshotCount, setSnapshotCount] = useState(0);
  const [hasArchive, setHasArchive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void api
      .getConstituentHistory(symbol, days, weight)
      .then((res) => {
        if (cancelled) return;
        setPoints(res.points ?? []);
        setSnapshotCount(res.snapshot_count ?? 0);
        setHasArchive(Boolean(res.has_research_archive));
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message || "Failed to load history");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbol, weight, days]);

  const dates = useMemo(() => points.map((p) => p.date), [points]);
  const sentiment = useMemo(
    () => points.map((p) => (p.sentiment_score != null ? p.sentiment_score : null)),
    [points],
  );
  const contribution = useMemo(
    () => points.map((p) => (p.contribution_proxy_pct != null ? p.contribution_proxy_pct : null)),
    [points],
  );

  useEffect(() => {
    if (!ref.current || !points.length) return;
    const theme = getChartTheme();
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    chart.setOption({
      backgroundColor: "transparent",
      grid: { left: 40, right: 12, top: 24, bottom: 28 },
      tooltip: {
        trigger: "axis",
        backgroundColor: theme.tooltipBg,
        borderColor: theme.tooltipBorder,
        textStyle: { color: theme.tooltipText, fontSize: 11 },
      },
      legend: {
        data: ["Sentiment", "Index contribution proxy"],
        textStyle: { color: theme.textColor, fontSize: 10 },
        top: 0,
      },
      xAxis: {
        type: "category",
        data: dates,
        axisLabel: { color: theme.textColor, fontSize: 9, rotate: dates.length > 14 ? 35 : 0 },
        axisLine: { lineStyle: { color: theme.axisColor } },
      },
      yAxis: [
        {
          type: "value",
          name: "Sentiment",
          nameTextStyle: { color: theme.textColor, fontSize: 9 },
          axisLabel: { color: theme.textColor, fontSize: 9 },
          splitLine: { lineStyle: { color: theme.gridColor } },
        },
        {
          type: "value",
          name: "Contrib %",
          nameTextStyle: { color: theme.textColor, fontSize: 9 },
          axisLabel: { color: theme.textColor, fontSize: 9, formatter: (v: number) => `${v.toFixed(2)}%` },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: "Sentiment",
          type: "line",
          data: sentiment,
          smooth: true,
          symbol: "circle",
          symbolSize: 4,
          lineStyle: { width: 2, color: theme.infoColor },
          itemStyle: { color: theme.infoColor },
        },
        {
          name: "Index contribution proxy",
          type: "line",
          yAxisIndex: 1,
          data: contribution,
          smooth: true,
          symbol: "none",
          lineStyle: { width: 1.5, type: "dashed", color: theme.maColors[1] },
        },
      ],
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [points, dates, sentiment, contribution, dark]);

  if (loading) {
    return <p className="text-[10px] text-muted-foreground">Loading {symbol} history…</p>;
  }
  if (error) {
    return <p className="text-[10px] text-red-600 dark:text-red-400">{error}</p>;
  }
  if (!points.length) {
    return (
      <p className="text-[10px] text-muted-foreground">
        No history yet for {symbol}. Daily archives build after each snapshot run.
      </p>
    );
  }

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
        <span>
          {snapshotCount > 0
            ? `${snapshotCount} archived research day${snapshotCount === 1 ? "" : "s"}`
            : "Price-based proxy trend"}
        </span>
        {!hasArchive ? (
          <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-amber-700 dark:text-amber-400">
            Research archive sparse — showing price proxy
          </span>
        ) : null}
      </div>
      <div ref={ref} className="h-[160px] w-full" />
      <p className="text-[9px] text-muted-foreground">
        Solid line = sentiment from archived company research. Dashed = estimated index contribution (weight × sentiment).
      </p>
    </div>
  );
}
