import { useEffect, useMemo, useRef, useState } from "react";
import { initEChart } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import { api, type ForecastEngineArtifactResponse } from "@/lib/api";
import { fmtNiftyLevel } from "@/lib/factorHistoryUtils";

const LABELS: Record<string, string> = {
  fii_net_5d: "FII net (5d, ₹ Cr)",
  dii_net_5d: "DII net (5d, ₹ Cr)",
};

interface Props {
  ticker?: string;
  onLoadState?: (available: boolean, error: string | null) => void;
}

/** p10/p50/p90 pct-return -> absolute Nifty price levels, using the artifact's own anchor spot
 * (the same conversion forecast_engine's artifact.py did in reverse when it wrote quantiles_pct). */
function quantilePriceLevels(spot: number, quantilesPct: Record<string, number>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [label, pct] of Object.entries(quantilesPct)) {
    out[label] = spot * (1 + pct / 100);
  }
  return out;
}

export function ForecastEngineChart({ ticker = "NIFTY", onLoadState }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const [artifact, setArtifact] = useState<ForecastEngineArtifactResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void api
      .getForecastEngineLatest(ticker)
      .then((res) => {
        if (!cancelled) {
          setArtifact(res);
          setError(null);
          onLoadState?.(res.available, null);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setArtifact(null);
          const msg = e instanceof Error ? e.message : "Forecast engine fetch failed";
          setError(msg);
          onLoadState?.(false, msg);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ticker, onLoadState]);

  const covariateKeys = artifact?.covariate_keys ?? [];
  const dataset = artifact?.dataset ?? [];

  const chartOption = useMemo(() => {
    if (!artifact?.available || !dataset.length) return null;
    const theme = getChartTheme();
    const dates = dataset.map((r) => String(r.date)).sort();

    const byFactor = new Map<string, Map<string, number>>();
    for (const factor of covariateKeys) {
      const m = new Map<string, number>();
      for (const row of dataset) {
        const v = row[factor];
        if (typeof v === "number" && Number.isFinite(v)) m.set(String(row.date), v);
      }
      byFactor.set(factor, m);
    }
    const niftyByDate = new Map(
      dataset
        .filter((r) => typeof r.nifty_close === "number" && Number.isFinite(r.nifty_close as number))
        .map((r) => [String(r.date), r.nifty_close as number]),
    );

    const priceLevels = quantilePriceLevels(artifact.spot ?? 0, artifact.quantiles_pct ?? {});
    const band = priceLevels.p10 != null && priceLevels.p90 != null ? [priceLevels.p10, priceLevels.p90] : null;

    return {
      tooltip: {
        trigger: "axis" as const,
        formatter: (params: unknown) => {
          const items = Array.isArray(params) ? params : [params];
          const idx = (items[0] as { dataIndex?: number })?.dataIndex ?? 0;
          const date = dates[idx] ?? "";
          const lines = items.map((p) => {
            const pt = p as { seriesName?: string; value?: number; marker?: string };
            if (pt.value == null || !Number.isFinite(Number(pt.value))) return "";
            if (pt.seriesName === "Nifty 50") return `${pt.marker ?? ""} Nifty 50: ${fmtNiftyLevel(Number(pt.value))}`;
            return `${pt.marker ?? ""} ${pt.seriesName}: ${Number(pt.value).toFixed(2)}`;
          });
          return [date, ...lines.filter(Boolean)].join("<br/>");
        },
      },
      legend: {
        data: [...covariateKeys.map((f) => LABELS[f] || f), "Nifty 50"],
        top: 0,
        textStyle: { color: theme.textColor, fontSize: 10 },
      },
      grid: { left: 52, right: 56, top: 36, bottom: 28 },
      xAxis: {
        type: "category" as const,
        data: dates.map((d) => d.slice(5)),
        axisLabel: { fontSize: 9, color: theme.textColor },
      },
      yAxis: [
        {
          type: "value" as const,
          scale: true,
          name: "Dataset factors",
          nameTextStyle: { fontSize: 9, color: theme.textColor },
          axisLabel: { fontSize: 9, color: theme.textColor },
        },
        {
          type: "value" as const,
          scale: true,
          name: "Nifty 50",
          position: "right" as const,
          nameTextStyle: { fontSize: 9, color: theme.textColor },
          axisLabel: { fontSize: 9, color: theme.textColor, formatter: (v: number) => fmtNiftyLevel(v) },
          splitLine: { show: false },
        },
      ],
      series: [
        ...covariateKeys.map((factor) => ({
          name: LABELS[factor] || factor,
          type: "line" as const,
          showSymbol: false,
          smooth: true,
          yAxisIndex: 0,
          data: dates.map((d) => byFactor.get(factor)?.get(d) ?? null),
        })),
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
          markLine:
            priceLevels.p50 != null
              ? {
                  silent: true,
                  symbol: "none",
                  lineStyle: { type: "dashed" as const, color: theme.upColor },
                  label: {
                    formatter: `TimesFM ${artifact.horizon_days ?? "?"}d forecast`,
                    fontSize: 9,
                    color: theme.textColor,
                  },
                  data: [{ yAxis: priceLevels.p50 }],
                }
              : undefined,
          markArea: band
            ? {
                silent: true,
                itemStyle: { color: theme.upColor, opacity: 0.08 },
                data: [[{ yAxis: band[0] }, { yAxis: band[1] }]],
              }
            : undefined,
        },
      ],
    };
  }, [artifact, dataset, covariateKeys, dark]);

  useEffect(() => {
    if (!ref.current || !chartOption) return;
    const chart = initEChart(ref.current);
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
        Forecast engine (TimesFM 3.0)
      </p>
      <p className="mt-1 text-[11px] text-muted-foreground">
        Dataset the model saw (left axis) vs Nifty 50 (right axis), with its point forecast (dashed
        line) and p10-p90 band shaded around the current spot.
      </p>
      {loading ? (
        <p className="mt-3 text-[11px] text-muted-foreground">Loading forecast engine artifact…</p>
      ) : error ? (
        <p className="mt-3 text-[11px] text-red-600 dark:text-red-400">{error}</p>
      ) : !artifact?.available ? (
        <div className="mt-3 space-y-1 text-[11px] text-muted-foreground">
          <p>No forecast_engine artifact yet for {ticker}.</p>
          <p>
            Run <code>python scripts/run_forecast_engine_latest.py</code> to generate one.
          </p>
        </div>
      ) : (
        <>
          <div ref={ref} className="mt-3 h-[240px] w-full" />
          <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] sm:grid-cols-4">
            <div>
              <p className="text-muted-foreground">As of</p>
              <p className="font-medium">{artifact.as_of}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Spot</p>
              <p className="font-medium">{fmtNiftyLevel(artifact.spot ?? 0)}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Expected return ({artifact.horizon_days}d)</p>
              <p className="font-medium">{(artifact.expected_return_pct ?? 0).toFixed(2)}%</p>
            </div>
            <div>
              <p className="text-muted-foreground">Recipe</p>
              <p className="font-medium">
                {artifact.recipe_name} v{artifact.recipe_version}
              </p>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
