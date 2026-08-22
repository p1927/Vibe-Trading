import { useEffect, useState } from "react";
import { api, type MarketFactorCoverageReport, type MarketFactorEntry } from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS_STYLE: Record<string, string> = {
  recorded: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  sourced: "bg-blue-500/15 text-blue-800 dark:text-blue-200",
  not_sourced: "bg-muted text-muted-foreground",
};

const STATUS_LABEL: Record<string, string> = {
  recorded: "Recorded",
  sourced: "Sourced (not yet recorded)",
  not_sourced: "Not sourced",
};

function FactorRow({ entry }: { entry: MarketFactorEntry }) {
  return (
    <div className="flex items-start justify-between gap-3 border-t px-3 py-2 text-[11px] first:border-t-0">
      <div className="min-w-0">
        <p className="font-medium text-foreground">
          {entry.name} <span className="text-muted-foreground">· {entry.category} · {entry.cadence}</span>
        </p>
        <p className="text-muted-foreground">{entry.gap_note || entry.source_note}</p>
      </div>
      <span className={cn("shrink-0 rounded-full px-2 py-0.5 font-medium", STATUS_STYLE[entry.status] ?? STATUS_STYLE.not_sourced)}>
        {STATUS_LABEL[entry.status] ?? entry.status}
      </span>
    </div>
  );
}

/** Read-only "do we have this factor for this market" view — fronts the `market_factor_catalog`
 * coverage report (`stock_simulator`'s `/history/market_factor_coverage`), which existed with no
 * frontend surface until this panel. One report call covers all 6 non-India markets; filtered
 * client-side to the selected `country` since the endpoint itself isn't country-scoped. */
export function MarketFactorCoveragePanel({ country }: { country: string }) {
  const [report, setReport] = useState<MarketFactorCoverageReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    api
      .getMarketFactorCoverage()
      .then((res) => setReport(res.data))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  if (error) return <p className="text-[11px] text-destructive">{error}</p>;
  if (!report) return <p className="text-[11px] text-muted-foreground">Loading…</p>;

  const entries = [...report.recorded, ...report.sourced, ...report.not_sourced]
    .filter((e) => e.market === country)
    .sort((a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name));

  if (entries.length === 0) {
    return <p className="text-[11px] text-muted-foreground">No catalogued factors for {country} yet.</p>;
  }

  return (
    <div className="overflow-hidden rounded-lg border bg-background/60">
      {entries.map((entry) => (
        <FactorRow key={`${entry.market}:${entry.name}`} entry={entry} />
      ))}
    </div>
  );
}
