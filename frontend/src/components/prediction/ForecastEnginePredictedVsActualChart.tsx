import { useEffect, useMemo, useRef, useState } from "react";
import { initEChart } from "@/lib/echarts";
import { getChartTheme } from "@/lib/chart-theme";
import { useDarkMode } from "@/hooks/useDarkMode";
import { api, type ForecastEngineEvaluationResponse } from "@/lib/api";
import { fmtNiftyLevel } from "@/lib/factorHistoryUtils";

interface Props {
  ticker?: string;
  recipe?: string;
}

export function ForecastEnginePredictedVsActualChart({ ticker = "NIFTY", recipe }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const [evaluation, setEvaluation] = useState<ForecastEngineEvaluationResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void api
      .getForecastEngineEvaluation(ticker, recipe)
      .then((res) => {
        if (!cancelled) {
          setEvaluation(res);
          setError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setEvaluation(null);
          setError(e instanceof Error ? e.message : "Evaluation history fetch failed");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ticker, recipe]);

  const observations = useMemo(
    () => [...(evaluation?.observations ?? [])].sort((a, b) => a.target_date.localeCompare(b.target_date)),
    [evaluation],
  );

  const chartOption = useMemo(() => {
    if (!evaluation?.available || !observations.length) return null;
    const theme = getChartTheme();
    const dates = observations.map((o) => o.target_date);

    const band = observations.map((o) =>
      o.predicted_p10 != null && o.predicted_p90 != null ? [o.predicted_p10, o.predicted_p90] : null,
    );

    return {
      tooltip: {
        trigger: "axis" as const,
        formatter: (params: unknown) => {
          const items = Array.isArray(params) ? params : [params];
          const idx = (items[0] as { dataIndex?: number })?.dataIndex ?? 0;
          const o = observations[idx];
          if (!o) return "";
          const lines = [
            `Forecast made: ${o.cutoff}`,
            `Actual: ${fmtNiftyLevel(o.actual)}`,
            `Predicted: ${fmtNiftyLevel(o.predicted_point)}`,
            o.predicted_p10 != null && o.predicted_p90 != null
              ? `Band: ${fmtNiftyLevel(o.predicted_p10)} - ${fmtNiftyLevel(o.predicted_p90)}`
              : "",
          ].filter(Boolean);
          return [o.target_date, ...lines].join("<br/>");
        },
      },
      legend: {
        data: ["Actual", "Predicted"],
        top: 0,
        textStyle: { color: theme.textColor, fontSize: 10 },
      },
      grid: { left: 56, right: 24, top: 36, bottom: 28 },
      xAxis: {
        type: "category" as const,
        data: dates.map((d) => d.slice(5)),
        axisLabel: { fontSize: 9, color: theme.textColor },
      },
      yAxis: {
        type: "value" as const,
        scale: true,
        axisLabel: { fontSize: 9, color: theme.textColor, formatter: (v: number) => fmtNiftyLevel(v) },
      },
      series: [
        {
          name: "Actual",
          type: "line" as const,
          showSymbol: true,
          symbolSize: 5,
          data: observations.map((o) => o.actual),
          lineStyle: { width: 2.5, color: theme.infoColor },
          itemStyle: { color: theme.infoColor },
          z: 10,
        },
        {
          name: "Predicted",
          type: "line" as const,
          showSymbol: true,
          symbolSize: 5,
          data: observations.map((o) => o.predicted_point),
          lineStyle: { width: 2, type: "dashed" as const, color: theme.upColor },
          itemStyle: { color: theme.upColor },
          z: 9,
          markArea: {
            silent: true,
            itemStyle: { color: theme.upColor, opacity: 0.08 },
            data: band.map((b, i) => (b ? [{ xAxis: i, yAxis: b[0] }, { xAxis: i + 1, yAxis: b[1] }] : [])).filter((d) => d.length),
          },
        },
      ],
    };
  }, [evaluation, observations, dark]);

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
        Predicted vs actual (backtest)
      </p>
      <p className="mt-1 text-[11px] text-muted-foreground">
        Every past forecast cutoff for this recipe: what TimesFM predicted for {evaluation?.horizon_days ?? "?"} days
        out (dashed, shaded p10-p90 band) vs what Nifty actually did (solid).
      </p>
      {loading ? (
        <p className="mt-3 text-[11px] text-muted-foreground">Loading evaluation history…</p>
      ) : error ? (
        <p className="mt-3 text-[11px] text-red-600 dark:text-red-400">{error}</p>
      ) : !evaluation?.available ? (
        <p className="mt-3 text-[11px] text-muted-foreground">
          No backtest history yet for this recipe — run the offline refresh job to generate one.
        </p>
      ) : (
        <>
          <div ref={ref} className="mt-3 h-[220px] w-full" />
          <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] sm:grid-cols-4">
            <div>
              <p className="text-muted-foreground">Observations</p>
              <p className="font-medium">
                {evaluation.n_observations}
                {evaluation.n_skipped_gaps ? ` (+${evaluation.n_skipped_gaps} skipped)` : ""}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground">Mean pinball loss</p>
              <p className="font-medium">{(evaluation.mean_pinball_loss ?? 0).toFixed(1)}</p>
            </div>
            <div>
              <p className="text-muted-foreground">vs naive baseline</p>
              <p className="font-medium">{(evaluation.mean_naive_pinball_loss ?? 0).toFixed(1)}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Beats naive?</p>
              <p className="font-medium">{evaluation.passes_gate ? "Yes" : "No"}</p>
            </div>
          </div>
          <p className="mt-2 text-[10px] text-muted-foreground">
            A small sample over a short window is a pipeline proof, not proof the recipe is good
            (docs/add/strategy.md rule 7) — read a low observation count that way.
          </p>
        </>
      )}
    </div>
  );
}
