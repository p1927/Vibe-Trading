import { useEffect, useMemo, useRef, useState } from "react";
import { initEChart } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import { api, type ForecastEngineArtifactResponse } from "@/lib/api";
import { fmtNiftyLevel } from "@/lib/factorHistoryUtils";
import { cn } from "@/lib/utils";

const LABELS: Record<string, string> = {
  fii_net_5d: "FII net (5d, ₹ Cr)",
  dii_net_5d: "DII net (5d, ₹ Cr)",
};

const NIFTY_KEY = "nifty_close";
const NIFTY_LABEL = "Nifty 50";

// A fixed, visually distinct palette for covariate lines — never the blue used for Nifty 50
// (theme.infoColor) or the amber used for the forecast markLine/band (theme.upColor), so no two
// series on this chart can end up looking the same regardless of how many covariates a recipe has.
const COVARIATE_COLORS = ["#f97316", "#a855f7", "#14b8a6", "#ec4899", "#84cc16", "#06b6d4"];

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

function fmtDatasetValue(v: unknown): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1000) return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
  return v.toFixed(2);
}

export function ForecastEngineChart({ ticker = "NIFTY", onLoadState }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const [artifact, setArtifact] = useState<ForecastEngineArtifactResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hiddenKeys, setHiddenKeys] = useState<Set<string>>(new Set());

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

  // Series filter (docs/add/forecast_engine.md's UI ask: pick which lines are on the chart).
  // Every key defaults to visible the first time this artifact's covariates are seen.
  const seriesKeys = useMemo(() => [...covariateKeys, NIFTY_KEY], [covariateKeys]);
  const seriesColors = useMemo(() => {
    const theme = getChartTheme();
    const map = new Map<string, string>();
    covariateKeys.forEach((key, i) => map.set(key, COVARIATE_COLORS[i % COVARIATE_COLORS.length]));
    map.set(NIFTY_KEY, theme.infoColor);
    return map;
  }, [covariateKeys, dark]);

  function toggleKey(key: string) {
    setHiddenKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

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
    const niftyVisible = !hiddenKeys.has(NIFTY_KEY);

    const covariateSeries = covariateKeys
      .filter((factor) => !hiddenKeys.has(factor))
      .map((factor) => {
        const color = seriesColors.get(factor);
        return {
          name: LABELS[factor] || factor,
          type: "line" as const,
          showSymbol: false,
          smooth: true,
          yAxisIndex: 0,
          data: dates.map((d) => byFactor.get(factor)?.get(d) ?? null),
          lineStyle: { width: 2, color },
          itemStyle: { color },
        };
      });

    const niftySeries = niftyVisible
      ? [
          {
            name: NIFTY_LABEL,
            type: "line" as const,
            showSymbol: false,
            smooth: true,
            yAxisIndex: 1,
            z: 10,
            data: dates.map((d) => niftyByDate.get(d) ?? null),
            lineStyle: { width: 2.5, color: seriesColors.get(NIFTY_KEY) },
            itemStyle: { color: seriesColors.get(NIFTY_KEY) },
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
                  itemStyle: { color: theme.upColor, opacity: 0.12 },
                  data: [[{ yAxis: band[0] }, { yAxis: band[1] }]],
                }
              : undefined,
          },
        ]
      : [];

    if (!covariateSeries.length && !niftySeries.length) return null;

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
            if (pt.seriesName === NIFTY_LABEL) return `${pt.marker ?? ""} Nifty 50: ${fmtNiftyLevel(Number(pt.value))}`;
            return `${pt.marker ?? ""} ${pt.seriesName}: ${Number(pt.value).toFixed(2)}`;
          });
          return [date, ...lines.filter(Boolean)].join("<br/>");
        },
      },
      legend: {
        data: [...covariateSeries.map((s) => s.name), ...niftySeries.map((s) => s.name)],
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
      series: [...covariateSeries, ...niftySeries],
    };
  }, [artifact, dataset, covariateKeys, hiddenKeys, seriesColors, dark]);

  useEffect(() => {
    if (!ref.current || !chartOption) return;
    const chart = initEChart(ref.current);
    chart.setOption(chartOption, true);
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
          <div className="mt-3 flex flex-wrap items-center gap-3">
            {seriesKeys.map((key) => {
              const label = key === NIFTY_KEY ? NIFTY_LABEL : LABELS[key] || key;
              const hidden = hiddenKeys.has(key);
              return (
                <label
                  key={key}
                  className="flex cursor-pointer select-none items-center gap-1.5 text-[11px] text-muted-foreground"
                >
                  <input
                    type="checkbox"
                    checked={!hidden}
                    onChange={() => toggleKey(key)}
                    className="h-3 w-3 accent-current"
                    style={{ accentColor: seriesColors.get(key) }}
                  />
                  <span
                    className="inline-block h-2 w-2 rounded-full"
                    style={{ backgroundColor: seriesColors.get(key), opacity: hidden ? 0.3 : 1 }}
                  />
                  <span className={cn(hidden && "line-through opacity-50")}>{label}</span>
                </label>
              );
            })}
          </div>

          {chartOption ? (
            <div ref={ref} className="mt-3 h-[240px] w-full" />
          ) : (
            <p className="mt-3 text-[11px] text-muted-foreground">All series hidden — check a box above to show one.</p>
          )}

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

          <p className="mt-4 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Dataset ({dataset.length} rows)
          </p>
          <div className="mt-2 max-h-64 overflow-y-auto overflow-x-auto rounded-xl border">
            <table className="w-full text-left text-[12px]">
              <thead className="sticky top-0 bg-muted/30">
                <tr className="border-b text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
                  <th className="px-3 py-2 font-semibold">Date</th>
                  {covariateKeys.map((key) => (
                    <th key={key} className="px-3 py-2 font-semibold">
                      {LABELS[key] || key}
                    </th>
                  ))}
                  <th className="px-3 py-2 font-semibold">Nifty close</th>
                </tr>
              </thead>
              <tbody>
                {[...dataset]
                  .sort((a, b) => String(a.date).localeCompare(String(b.date)))
                  .map((row) => (
                    <tr key={String(row.date)} className="border-b last:border-0 hover:bg-muted/20">
                      <td className="px-3 py-2 font-mono text-[11px]">{String(row.date)}</td>
                      {covariateKeys.map((key) => (
                        <td key={key} className="px-3 py-2">
                          {fmtDatasetValue(row[key])}
                        </td>
                      ))}
                      <td className="px-3 py-2">{fmtDatasetValue(row.nifty_close)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
