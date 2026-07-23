import { NiftyForecastReplayChart } from "@/components/charts/NiftyForecastReplayChart";
import type { ExternalPredictionRecord } from "@/lib/api";
import { recordToLiveForecast, effectiveChartHorizonDays } from "@/lib/externalPredictionsUtils";

const DIRECTION_COLORS: Record<string, string> = {
  bullish: "#22c55e",
  bearish: "#ef4444",
  neutral: "#94a3b8",
};

interface Props {
  record: ExternalPredictionRecord;
  horizonDays: number;
  priceSeries?: Array<{ date?: string; close?: number | null }>;
  priceLoading?: boolean;
  height?: number;
}

export function ExternalPredictionReplayChart({
  record,
  horizonDays,
  priceSeries,
  priceLoading,
  height = 280,
}: Props) {
  const liveForecast = recordToLiveForecast(record);
  const chartHorizonDays = effectiveChartHorizonDays(record, horizonDays);
  if (!liveForecast) {
    return (
      <div className="flex h-[220px] items-center justify-center rounded-lg bg-muted/20 text-[11px] text-muted-foreground">
        No forecast levels to chart
      </div>
    );
  }

  return (
    <NiftyForecastReplayChart
      horizonDays={chartHorizonDays}
      priceSeries={priceSeries}
      priceLoading={priceLoading}
      liveForecast={liveForecast}
      predictedLineColor={DIRECTION_COLORS[record.direction ?? "neutral"] ?? DIRECTION_COLORS.neutral}
      emptyForecastHint="NIFTY 50 index projection from source"
      height={height}
    />
  );
}
