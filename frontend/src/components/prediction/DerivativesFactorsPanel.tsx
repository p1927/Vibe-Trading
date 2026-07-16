import { useEffect, useMemo, useRef, useState } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import { api, type IndexFactorHistoryPoint } from "@/lib/api";
import {
  DERIVATIVES_FACTORS,
  NIFTY_CLOSE_FACTOR,
  factorSeriesValues,
  fmtNiftyLevel,
  isLongFactorSeries,
  niftyCloseSeries,
  pivotFactorHistoryWide,
} from "@/lib/factorHistoryUtils";

const LABELS: Record<string, string> = {
  nifty_pcr: "Nifty PCR",
  fii_net_5d: "FII net (5d, ₹ Cr)",
  dii_net_5d: "DII net (5d, ₹ Cr)",
  fii_fut_long_short_ratio: "FII index fut long/short",
};

interface Props {
  days?: number;
  onLoadState?: (pointCount: number, error: string | null) => void;
}

export function DerivativesFactorsPanel({ days = 365, onLoadState }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const [series, setSeries] = useState<IndexFactorHistoryPoint[]>([]);
  const [coverageNotes, setCoverageNotes] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void api
      .getIndexDerivativesHistory(days, [...DERIVATIVES_FACTORS, NIFTY_CLOSE_FACTOR])
      .then((res) => {
        if (!cancelled) {
          const pts = res.series ?? [];
          setSeries(pts);
          setCoverageNotes(res.coverage_notes ?? []);
          onLoadState?.(pts.length, null);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setSeries([]);
          setCoverageNotes([]);
          onLoadState?.(0, e instanceof Error ? e.message : "Derivatives history failed");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [days, onLoadState]);

  const wide = useMemo(() => pivotFactorHistoryWide(series), [series]);

  const chartOption = useMemo(() => {
    const theme = getChartTheme();
    const factors = [...DERIVATIVES_FACTORS];
    let dates: string[];

    if (isLongFactorSeries(series)) {
      dates = [...new Set(series.map((p) => p.date).filter(Boolean))].sort();
    } else {
      dates = wide.map((r) => String(r.date)).sort();
    }

    const byFactor = new Map<string, Map<string, number>>();
    for (const factor of factors) {
      for (const { date, value } of factorSeriesValues(wide.length ? wide : series, factor)) {
        if (value == null) continue;
        if (!byFactor.has(factor)) byFactor.set(factor, new Map());
        byFactor.get(factor)!.set(date, value);
      }
    }

    const niftyByDate = new Map(
      niftyCloseSeries(wide.length ? wide : pivotFactorHistoryWide(series))
        .filter((r) => r.close != null)
        .map((r) => [r.date, r.close as number]),
    );

    const hasData = factors.some((f) => (byFactor.get(f)?.size ?? 0) > 0);
    const hasNifty = niftyByDate.size > 0;

    if (!hasData && !hasNifty) return null;

    const legendData = [
      ...factors.map((f) => LABELS[f] || f),
      ...(hasNifty ? ["Nifty 50"] : []),
    ];

    return {
      tooltip: {
        trigger: "axis" as const,
        formatter: (params: unknown) => {
          const items = Array.isArray(params) ? params : [params];
          const idx = (items[0] as { dataIndex?: number })?.dataIndex ?? 0;
          const date = dates[idx] ?? "";
          const lines = items.map((p) => {
            const pt = p as { seriesName?: string; value?: number; marker?: string };
            const val = pt.value;
            if (val == null || !Number.isFinite(Number(val))) return "";
            if (pt.seriesName === "Nifty 50") {
              return `${pt.marker ?? ""} Nifty 50: ${fmtNiftyLevel(Number(val))}`;
            }
            return `${pt.marker ?? ""} ${pt.seriesName}: ${Number(val).toFixed(2)}`;
          });
          return [date, ...lines.filter(Boolean)].join("<br/>");
        },
      },
      legend: {
        data: legendData,
        top: 0,
        textStyle: { color: theme.textColor, fontSize: 10 },
      },
      grid: { left: 52, right: hasNifty ? 56 : 24, top: 36, bottom: 28 },
      xAxis: {
        type: "category" as const,
        data: dates.map((d) => d.slice(5)),
        axisLabel: { fontSize: 9, color: theme.textColor },
      },
      yAxis: [
        {
          type: "value" as const,
          scale: true,
          name: "Flows / PCR",
          nameTextStyle: { fontSize: 9, color: theme.textColor },
          axisLabel: { fontSize: 9, color: theme.textColor },
        },
        ...(hasNifty
          ? [
              {
                type: "value" as const,
                scale: true,
                name: "Nifty 50",
                position: "right" as const,
                nameTextStyle: { fontSize: 9, color: theme.textColor },
                axisLabel: {
                  fontSize: 9,
                  color: theme.textColor,
                  formatter: (v: number) => fmtNiftyLevel(v),
                },
                splitLine: { show: false },
              },
            ]
          : []),
      ],
      series: [
        ...factors.map((factor) => ({
          name: LABELS[factor] || factor,
          type: "line" as const,
          showSymbol: false,
          smooth: true,
          yAxisIndex: 0,
          data: dates.map((d) => byFactor.get(factor)?.get(d) ?? null),
        })),
        ...(hasNifty
          ? [
              {
                name: "Nifty 50",
                type: "line" as const,
                showSymbol: false,
                smooth: true,
                yAxisIndex: 1,
                z: 10,
                data: dates.map((d) => niftyByDate.get(d) ?? null),
                lineStyle: { width: 2.5, color: theme.infoColor },
                itemStyle: { color: theme.infoColor },
              },
            ]
          : []),
      ],
    };
  }, [series, wide, dark]);

  useEffect(() => {
    if (!ref.current || !chartOption) return;
    const chart = echarts.init(ref.current);
    chart.setOption(chartOption);
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [chartOption]);

  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        Derivatives &amp; flows (12 months)
      </p>
      <p className="mt-1 text-[11px] text-muted-foreground">
        PCR, FII/DII 5-day nets, and FII index futures positioning vs Nifty 50 (right axis) — compare
        flow shifts with index moves on crash days.
      </p>
      {loading ? (
        <p className="mt-3 text-[11px] text-muted-foreground">Loading derivatives factor history…</p>
      ) : !chartOption ? (
        <div className="mt-3 space-y-1 text-[11px] text-muted-foreground">
          <p>No derivatives history in factor store for this window.</p>
          <p>Run factor backfill (PCR / participant OI / FII cash) then refresh.</p>
        </div>
      ) : (
        <div ref={ref} className="mt-3 h-[240px] w-full" />
      )}
      {coverageNotes.length ? (
        <ul className="mt-2 space-y-0.5 text-[10px] text-amber-700 dark:text-amber-400">
          {coverageNotes.map((n) => (
            <li key={n}>• {n}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
