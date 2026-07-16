import { useEffect, useMemo, useRef } from "react";
import { echarts } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { IndexFactorHistoryPoint } from "@/lib/api";
import { pivotFactorHistoryWide, niftyPctChangeSeries, niftyCloseAt, fmtNiftyLevel } from "@/lib/factorHistoryUtils";

const NIFTY_COLOR = "#1e293b";

interface Props {
  series: IndexFactorHistoryPoint[];
  factors?: string[];
  height?: number;
  coverageNotes?: string[];
  showNiftyOverlay?: boolean;
}

const COLORS = ["#3b82f6", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#06b6d4", "#ec4899", "#14b8a6"];

const LABELS: Record<string, string> = {
  oil_brent: "Brent",
  oil_wti: "WTI",
  india_vix: "India VIX",
  sp500: "S&P 500",
  us_10y: "US 10Y",
  usd_inr: "USD/INR",
  gold: "Gold",
  fii_net_5d: "FII net 5d",
  dii_net_5d: "DII net 5d",
  nifty_pcr: "PCR",
  repo_rate: "Repo",
};

export function IndexFactorTimelineChart({
  series = [],
  factors,
  height = 220,
  coverageNotes,
  showNiftyOverlay = true,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();

  const wide = useMemo(() => pivotFactorHistoryWide(series), [series]);

  const keys = useMemo(() => {
    if (factors?.length) {
      return factors.filter((k) => k !== "date" && k !== "nifty_close");
    }
    const set = new Set<string>();
    for (const row of wide) {
      for (const k of Object.keys(row)) {
        if (k !== "date" && k !== "nifty_close" && typeof row[k] === "number") set.add(k);
      }
    }
    return Array.from(set).slice(0, 8);
  }, [wide, factors]);

  const { dates, normalized } = useMemo(() => {
    const bases: Record<string, number> = {};
    for (const key of keys) {
      for (const row of wide) {
        const v = row[key];
        if (typeof v === "number" && Number.isFinite(v) && v !== 0) {
          bases[key] = v;
          break;
        }
      }
    }
    const dates = wide.map((r) => String(r.date).slice(5));
    const normalized: Record<string, (number | null)[]> = {};
    for (const key of keys) {
      const base = bases[key];
      normalized[key] = wide.map((row) => {
        const v = row[key];
        if (typeof v !== "number" || !Number.isFinite(v) || !base) return null;
        return ((v / base) - 1) * 100;
      });
    }
    return { dates, normalized };
  }, [wide, keys]);

  const niftyOverlay = useMemo(() => niftyPctChangeSeries(wide), [wide]);
  const hasNifty = showNiftyOverlay && niftyOverlay.pctChange.some((v) => v != null);

  useEffect(() => {
    if (!ref.current || !wide.length || !keys.length) return;
    const t = getChartTheme();
    const chart = echarts.init(ref.current);

    const legendNames = [
      ...(hasNifty ? ["Nifty 50"] : []),
      ...keys.map((k) => LABELS[k] || k),
    ];

    chart.setOption({
      backgroundColor: "transparent",
      title: {
        text: hasNifty
          ? "Macro drivers vs Nifty 50 (% change from period start)"
          : "Macro drivers (% change from period start)",
        left: 0,
        textStyle: { fontSize: 11, color: t.textColor, fontWeight: 600 },
      },
      legend: {
        data: legendNames,
        top: 4,
        right: 0,
        textStyle: { fontSize: 9, color: t.textColor },
      },
      grid: { left: 48, right: 12, top: 48, bottom: 32 },
      tooltip: {
        trigger: "axis",
        formatter: (params: unknown) => {
          const items = Array.isArray(params) ? params : [params];
          const idx = (items[0] as { dataIndex?: number })?.dataIndex ?? 0;
          const date = wide[idx]?.date ? String(wide[idx].date).slice(0, 10) : "";
          const lines = items.map((p) => {
            const pt = p as { seriesName?: string; value?: number; marker?: string };
            const val = pt.value;
            if (val == null || !Number.isFinite(Number(val))) return "";
            if (pt.seriesName === "Nifty 50") {
              const close = niftyCloseAt(wide, idx);
              return `${pt.marker ?? ""} Nifty 50: ${Number(val).toFixed(2)}%${close != null ? ` (${fmtNiftyLevel(close)})` : ""}`;
            }
            return `${pt.marker ?? ""} ${pt.seriesName}: ${Number(val).toFixed(2)}%`;
          });
          return [date, ...lines.filter(Boolean)].join("<br/>");
        },
      },
      xAxis: {
        type: "category",
        data: dates,
        axisLabel: { fontSize: 9, color: t.textColor, rotate: dates.length > 12 ? 35 : 0 },
      },
      yAxis: {
        type: "value",
        scale: true,
        name: "Δ% from start",
        nameTextStyle: { fontSize: 9, color: t.textColor },
        axisLabel: { fontSize: 10, color: t.textColor, formatter: "{value}%" },
      },
      series: [
        ...(hasNifty
          ? [
              {
                name: "Nifty 50",
                type: "line" as const,
                smooth: true,
                showSymbol: false,
                z: 10,
                data: niftyOverlay.pctChange,
                lineStyle: { width: 2.5, color: dark ? "#e2e8f0" : NIFTY_COLOR },
                itemStyle: { color: dark ? "#e2e8f0" : NIFTY_COLOR },
              },
            ]
          : []),
        ...keys.map((key, i) => ({
          name: LABELS[key] || key,
          type: "line" as const,
          smooth: true,
          showSymbol: false,
          data: normalized[key],
          lineStyle: { width: 1.5, color: COLORS[i % COLORS.length] },
          itemStyle: { color: COLORS[i % COLORS.length] },
        })),
      ],
    });

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [wide, keys, dates, normalized, dark, hasNifty, niftyOverlay]);

  if (!wide.length) {
    return (
      <div
        className="flex flex-col items-center justify-center gap-2 rounded-xl border bg-card px-4 text-center text-[12px] text-muted-foreground"
        style={{ height }}
      >
        <p>No macro factor history in store yet.</p>
        <p className="text-[11px]">Run factor enrichment backfill, then refresh this page.</p>
      </div>
    );
  }

  if (!keys.length) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border bg-card text-[12px] text-muted-foreground"
        style={{ height }}
      >
        Selected factors have no numeric history in this window.
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <div ref={ref} style={{ height }} className="rounded-xl border bg-card p-2" />
      {coverageNotes?.length ? (
        <ul className="text-[10px] text-amber-700 dark:text-amber-400">
          {coverageNotes.map((n) => (
            <li key={n}>• {n}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
