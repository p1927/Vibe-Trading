import { ExternalLink } from "lucide-react";
import type { ExternalPredictionRecord, ExternalPredictionSource } from "@/lib/api";
import { formatFetchMethodLabel, formatPageKindLabel } from "@/lib/externalPredictionsUtils";
import { cn } from "@/lib/utils";

interface Props {
  record: ExternalPredictionRecord;
  source?: ExternalPredictionSource;
  className?: string;
}

export function ExternalPredictionIssueCard({ record, source, className }: Props) {
  const name = source?.display_name || record.source_id;
  const url = record.provenance?.url;
  const pageKindLabel = formatPageKindLabel(record.provenance?.page_kind as string | undefined);
  const fetchMethodLabel = formatFetchMethodLabel(record.provenance?.fetch_method as string | undefined);
  const status = record.fetch_status || "unknown";
  const statusLabel = status === "error" ? "Crawl error" : status === "not_found" ? "No forecast" : status;
  const statusClass =
    status === "error"
      ? "bg-red-500/15 text-red-700 dark:text-red-300"
      : "bg-amber-500/15 text-amber-800 dark:text-amber-300";

  return (
    <article
      className={cn(
        "rounded-lg border border-dashed border-border/70 bg-muted/15 p-3",
        className,
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-foreground">{name}</h3>
            <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-semibold", statusClass)}>
              {statusLabel}
            </span>
            {pageKindLabel ? (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                {pageKindLabel}
              </span>
            ) : null}
            {fetchMethodLabel ? (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                {fetchMethodLabel}
              </span>
            ) : null}
          </div>
          {record.error_message ? (
            <p className="mt-1 text-[11px] text-foreground/85">{record.error_message}</p>
          ) : (
            <p className="mt-1 text-[11px] text-muted-foreground">No NIFTY 50 index forecast extracted for this horizon.</p>
          )}
          {url ? (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-2 inline-flex max-w-full items-center gap-1 text-[11px] font-medium text-primary hover:underline"
            >
              <span className="truncate">{record.provenance?.title?.slice(0, 100) || url.slice(0, 100)}</span>
              <ExternalLink className="h-3 w-3 shrink-0" />
            </a>
          ) : null}
        </div>
      </div>
    </article>
  );
}
