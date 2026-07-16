import { useEffect, useMemo, useRef, useState } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import { DayMoveCauses } from "@/components/prediction/DayMoveCauses";
import { api, type DayAttributionResponse, type IndexBacktestDrawdown, type IndexDayAttribution } from "@/lib/api";

export interface NiftyHistoryPoint {
  date: string;
  close: number;
  realized_1d_pct?: number | null;
}

interface Props {
  series: NiftyHistoryPoint[];
  majorDrawdowns?: IndexBacktestDrawdown[];
  height?: number;
}

function fmtLevel(v: number): string {
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function NiftyHistoricalChart({ series = [], majorDrawdowns = [], height = 280 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [attribution, setAttribution] = useState<IndexDayAttribution | null>(null);
  const [loadingAttr, setLoadingAttr] = useState(false);
  const [attrError, setAttrError] = useState<string | null>(null);

  const drawdownByDate = useMemo(() => {
    const map = new Map<string, IndexBacktestDrawdown>();
    for (const row of majorDrawdowns) {
      if (row.date) map.set(row.date, row);
    }
    return map;
  }, [majorDrawdowns]);

  const bigMoveIndices = useMemo(
    () =>
      series
        .map((p, i) => ({ i, move: Math.abs(p.realized_1d_pct ?? 0) }))
        .filter((x) => x.move >= 0.75)
        .map((x) => x.i),
    [series],
  );

  useEffect(() => {
    if (!selectedDate) {
      setAttribution(null);
      return;
    }
    const cached = drawdownByDate.get(selectedDate);
    let cancelled = false;
    setLoadingAttr(true);
    setAttrError(null);

    if (cached?.causal_hypotheses?.length) {
      setAttribution({
        date: cached.date,
        close: cached.spot,
        realized_1d_pct: cached.realized_1d_pct,
        factor_drivers: cached.factor_drivers,
        calendar_events: cached.calendar_events,
        causal_hypotheses: cached.causal_hypotheses,
        index_headlines: cached.index_headlines,
      });
    }

    void api
      .getIndexDayAttribution(selectedDate)
      .then((res: DayAttributionResponse) => {
        if (cancelled) return;
        if (res.status === "ok" && res.attribution) {
          setAttribution(res.attribution);
        } else if (!cached?.causal_hypotheses?.length) {
          setAttrError(res.message || "No attribution for this date");
          setAttribution(null);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled && !cached?.causal_hypotheses?.length) {
          setAttrError(e instanceof Error ? e.message : "Failed to load day attribution");
          setAttribution(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingAttr(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedDate, drawdownByDate]);

  useEffect(() => {
    if (!ref.current || !series.length) return;
    const t = getChartTheme();
    const chart = echarts.init(ref.current);

    const labels = series.map((p) => p.date.slice(5));
    const closes = series.map((p) => p.close);
    const markPoints = bigMoveIndices.map((idx) => {
      const p = series[idx];
      const move = p.realized_1d_pct ?? 0;
      return {
        name: p.date,
        coord: [idx, p.close],
        value: fmtPct(move),
        itemStyle: { color: move < 0 ? t.downColor : t.upColor },
      };
    });

    chart.setOption({
      backgroundColor: "transparent",
      title: {
        text: "NIFTY history — click a day to see what caused the move",
        left: 0,
        textStyle: { fontSize: 11, color: t.textColor, fontWeight: 600 },
      },
      grid: { left: 56, right: 12, top: 36, bottom: 32 },
      tooltip: {
        trigger: "axis",
        formatter: (params: unknown) => {
          const row = Array.isArray(params) ? params[0] : params;
          const idx = row?.dataIndex ?? 0;
          const p = series[idx];
          if (!p) return "";
          return `${p.date}<br/>Close ${fmtLevel(p.close)}<br/>1d ${fmtPct(p.realized_1d_pct)}<br/><span style="opacity:0.8">Click for causes</span>`;
        },
      },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: { fontSize: 9, color: t.textColor, rotate: labels.length > 20 ? 35 : 0 },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: { fontSize: 10, color: t.textColor, formatter: (v: number) => fmtLevel(v) },
      },
      series: [
        {
          name: "NIFTY close",
          type: "line",
          data: closes,
          smooth: true,
          showSymbol: true,
          symbolSize: 4,
          lineStyle: { color: t.infoColor, width: 2 },
          itemStyle: { color: t.infoColor },
          markPoint: markPoints.length
            ? { symbol: "circle", symbolSize: 10, data: markPoints, label: { show: false } }
            : undefined,
        },
      ],
    });

    chart.off("click");
    chart.on("click", (params) => {
      const idx = params.dataIndex;
      if (idx == null || idx < 0 || idx >= series.length) return;
      setSelectedDate(series[idx].date);
    });

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [series, bigMoveIndices, dark]);

  const drawdownRow = selectedDate ? drawdownByDate.get(selectedDate) : undefined;
  const constituentMovers = drawdownRow?.constituent_movers ?? drawdownRow?.worst_contributors ?? [];

  if (!series.length) {
    return (
      <div className="rounded-lg border border-dashed p-4 text-[12px] text-muted-foreground">
        NIFTY price history unavailable — re-run backtest to populate aligned factor data.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div ref={ref} style={{ height }} className="w-full rounded-lg border border-border/40 bg-muted/10" />
      {selectedDate ? (
        <div className="rounded-lg border bg-muted/20 p-3 text-[11px]">
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
            <p className="font-semibold">
              {selectedDate}
              {attribution?.close != null ? ` · ${fmtLevel(attribution.close)}` : ""}
              {attribution?.realized_1d_pct != null ? (
                <span
                  className={
                    attribution.realized_1d_pct < 0
                      ? " ml-2 text-red-600 dark:text-red-400"
                      : " ml-2 text-emerald-600 dark:text-emerald-400"
                  }
                >
                  {fmtPct(attribution.realized_1d_pct)} (1d)
                </span>
              ) : null}
            </p>
            <button
              type="button"
              className="text-[10px] text-muted-foreground hover:text-foreground"
              onClick={() => setSelectedDate(null)}
            >
              Close
            </button>
          </div>
          {loadingAttr ? <p className="mb-2 text-muted-foreground">Loading causes and headlines…</p> : null}
          {attrError ? <p className="mb-2 text-amber-700 dark:text-amber-400">{attrError}</p> : null}
          <DayMoveCauses attribution={attribution} />
          {constituentMovers.length ? (
            <div className="mt-3 border-t pt-3">
              <p className="mb-1 font-medium text-muted-foreground">Heavyweight stocks that day</p>
              <ul className="space-y-1">
                {constituentMovers.slice(0, 6).map((m) => (
                  <li key={m.symbol}>
                    <span className="font-medium">{m.symbol}</span> {fmtPct(m.return_1d_pct)} (
                    {m.weight_pct?.toFixed(1)}% wt · index {fmtPct(m.index_contribution_pct)})
                    {(m.headlines ?? []).length ? (
                      <ul className="ml-3 mt-0.5 text-muted-foreground">
                        {m.headlines!.map((h) => (
                          <li key={h.title}>“{h.title}”</li>
                        ))}
                      </ul>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : (
        <p className="text-[10px] text-muted-foreground">
          Pin markers highlight |1d move| ≥ 0.75%. Click any point for ranked causes (flows, oil, global
          risk, news) — not just the raw factor numbers.
        </p>
      )}
    </div>
  );
}
