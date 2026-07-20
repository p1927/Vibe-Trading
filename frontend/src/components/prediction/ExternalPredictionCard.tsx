import { ExternalLink } from "lucide-react";
import { ExternalPredictionReplayChart } from "@/components/charts/ExternalPredictionReplayChart";
import type { ExternalPredictionRecord, ExternalPredictionSource } from "@/lib/api";
import { formatHorizonMatch } from "@/lib/externalPredictionsUtils";
import { cn } from "@/lib/utils";

const KIND_LABELS: Record<string, string> = {
  media: "Media",
  broker: "Broker",
  global_bank: "Global bank",
};

function fmtLevel(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function fmtDate(iso: string | undefined): string {
  if (!iso) return "—";
  const d = new Date(`${iso.slice(0, 10)}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

interface Props {
  record: ExternalPredictionRecord;
  source?: ExternalPredictionSource;
  horizonDays: number;
  priceSeries?: Array<{ date?: string; close?: number | null }>;
  priceLoading?: boolean;
  className?: string;
}

export function ExternalPredictionCard({
  record,
  source,
  horizonDays,
  priceSeries,
  priceLoading,
  className,
}: Props) {
  const name = source?.display_name || record.source_id;
  const url = record.provenance?.url;
  const horizonLabel = formatHorizonMatch(record);

  return (
    <article
      className={cn(
        "flex flex-col gap-3 rounded-xl border border-border/60 bg-card/40 p-4 shadow-sm",
        className,
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-foreground">{name}</h3>
            {source?.kind ? (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                {KIND_LABELS[source.kind] || source.kind}
              </span>
            ) : null}
          </div>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            NIFTY 50 index · Spot {fmtLevel(record.spot_at_fetch)} · Published {fmtDate(record.published_at)}
          </p>
          {horizonLabel ? (
            <p className="mt-0.5 text-[10px] text-muted-foreground">{horizonLabel}</p>
          ) : null}
          {url ? (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-2 inline-flex max-w-full flex-col gap-0.5 text-[11px] font-medium text-primary hover:underline"
            >
              <span className="inline-flex items-center gap-1">
                {record.provenance?.title ? record.provenance.title.slice(0, 120) : "Source article"}
                <ExternalLink className="h-3 w-3 shrink-0" />
              </span>
              <span className="break-all text-[10px] font-normal text-muted-foreground">{url}</span>
            </a>
          ) : null}
        </div>
        {record.direction ? (
          <span
            className={cn(
              "rounded-md px-2 py-1 text-[11px] font-semibold capitalize",
              record.direction === "bullish" && "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
              record.direction === "bearish" && "bg-red-500/10 text-red-700 dark:text-red-300",
              record.direction === "neutral" && "bg-muted text-muted-foreground",
            )}
          >
            {record.direction}
          </span>
        ) : null}
      </div>

      <ExternalPredictionReplayChart
        record={record}
        horizonDays={horizonDays}
        priceSeries={priceSeries}
        priceLoading={priceLoading}
        height={280}
      />

      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <div className="space-y-1 rounded-lg bg-muted/30 p-2.5 text-[11px]">
          <p>
            Target mid: <span className="font-semibold">{fmtLevel(record.target?.mid)}</span>
          </p>
          <p>
            Range: {fmtLevel(record.target?.low)} – {fmtLevel(record.target?.high)}
          </p>
          <p>Expected return: {fmtPct(record.expected_return_pct)}</p>
          <p>Target date: {fmtDate(record.target_date)}</p>
        </div>
        <div className="flex flex-col gap-2 text-[12px]">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Rationale</p>
          {record.provenance?.summary ? (
            <p className="text-[12px] leading-relaxed text-foreground/90">{record.provenance.summary}</p>
          ) : null}
          <ul className="list-disc space-y-1.5 ps-4 text-foreground/90">
            {(record.rationale_bullets?.length ? record.rationale_bullets : ["No rationale extracted"]).map(
              (bullet) => (
                <li key={bullet}>{bullet}</li>
              ),
            )}
          </ul>
        </div>
      </div>
    </article>
  );
}
