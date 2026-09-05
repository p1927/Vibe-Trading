import { ExternalLink, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { HubNewsCalendarEvent, HubNewsImpactFigures, IndexUpcomingEvent } from "@/lib/api";

export type CalendarEventItem =
  | { kind: "news"; key: string; data: HubNewsCalendarEvent }
  | { kind: "calendar"; key: string; data: IndexUpcomingEvent };

function returnDirection(returnPct?: number): "up" | "down" | "flat" | null {
  if (returnPct === undefined || returnPct === null) return null;
  if (returnPct > 0) return "up";
  if (returnPct < 0) return "down";
  return "flat";
}

function formatReturnPct(returnPct?: number): string | null {
  if (returnPct === undefined || returnPct === null) return null;
  const sign = returnPct > 0 ? "+" : "";
  return `${sign}${returnPct.toFixed(2)}%`;
}

function directionTone(direction: "up" | "down" | "flat" | null): string {
  if (direction === "up") return "text-emerald-700 dark:text-emerald-400";
  if (direction === "down") return "text-red-700 dark:text-red-400";
  return "text-muted-foreground";
}

function isPastDate(dateStr?: string): boolean {
  if (!dateStr) return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(dateStr.trim());
  if (!match) return false;
  const d = new Date(parseInt(match[1], 10), parseInt(match[2], 10) - 1, parseInt(match[3], 10));
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return d.getTime() < today.getTime();
}

function EventResultBlock({
  predicted,
  actual,
}: {
  predicted?: HubNewsImpactFigures | null;
  actual?: HubNewsImpactFigures | null;
}) {
  const actualReturn = actual?.return_pct as number | undefined;
  const predictedReturn = predicted?.return_pct as number | undefined;
  const hasActual = actualReturn !== undefined && actualReturn !== null;
  const actualDirection = returnDirection(actualReturn);

  if (!hasActual) {
    return (
      <p className="rounded-md border border-dashed bg-muted/10 px-2.5 py-2 text-[12px] text-muted-foreground">
        Outcome not yet reconciled from the news hub for this event.
      </p>
    );
  }

  return (
    <div className="rounded-md border bg-muted/10 px-2.5 py-2 text-[12px]">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-muted-foreground">Actual move:</span>
        <span className={cn("font-semibold tabular-nums", directionTone(actualDirection))}>
          {formatReturnPct(actualReturn)}
        </span>
        {predictedReturn !== undefined && predictedReturn !== null ? (
          <span className="text-muted-foreground">
            (predicted {formatReturnPct(predictedReturn)})
          </span>
        ) : null}
      </div>
    </div>
  );
}

function dateConfidenceBadge(confidence?: string) {
  if (!confidence) return null;
  const value = confidence.toLowerCase();
  const tone =
    value === "high"
      ? "text-emerald-700 dark:text-emerald-400 bg-emerald-500/10"
      : value === "medium"
        ? "text-amber-700 dark:text-amber-400 bg-amber-500/10"
        : "text-muted-foreground bg-muted";
  return (
    <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium capitalize", tone)}>
      {value} confidence
    </span>
  );
}

function verificationStatusBadge(status?: string) {
  if (!status) return null;
  const value = status.toLowerCase();
  const label = value.replace(/_/g, " ");
  const tone =
    value === "calendar_corroborated"
      ? "text-emerald-700 dark:text-emerald-400 bg-emerald-500/10"
      : value === "multi_source"
        ? "text-blue-700 dark:text-blue-400 bg-blue-500/10"
        : "text-amber-700 dark:text-amber-400 bg-amber-500/10";
  return (
    <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium capitalize", tone)}>
      {label}
    </span>
  );
}

function NewsEventDetail({ event }: { event: HubNewsCalendarEvent }) {
  const factCheck = event.fact_check;

  return (
    <>
      <div>
        <h3 className="text-sm font-semibold leading-snug text-foreground">
          {event.event || "Upcoming event"}
        </h3>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {event.date ? (
            <span className="text-[11px] tabular-nums text-muted-foreground">{event.date}</span>
          ) : event.timeline_phrase ? (
            <span className="text-[11px] text-muted-foreground">{event.timeline_phrase}</span>
          ) : null}
          {dateConfidenceBadge(event.date_confidence)}
          {verificationStatusBadge(event.verification_status)}
          {event.type ? (
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
              {event.type}
            </span>
          ) : null}
        </div>
      </div>

      {event.index_impact_mechanism ? (
        <div>
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Impact on Nifty 50
          </p>
          <p className="rounded-md border bg-muted/10 px-2.5 py-2 text-[12px] leading-snug text-foreground/90">
            {event.index_impact_mechanism}
          </p>
        </div>
      ) : null}

      {factCheck ? (
        <div>
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Date verification
          </p>
          <div className="rounded-md border bg-muted/10 px-2.5 py-2 text-[12px]">
            <div className="flex flex-wrap items-center gap-1.5">
              <span
                className={cn(
                  "rounded-full px-2 py-0.5 text-[10px] font-medium capitalize",
                  factCheck.status === "confirmed"
                    ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                    : "bg-amber-500/10 text-amber-700 dark:text-amber-400",
                )}
              >
                {(factCheck.status || "not yet confirmed").replace(/_/g, " ")}
              </span>
              {factCheck.confirmed_date ? (
                <span className="text-[10px] tabular-nums text-muted-foreground">
                  {factCheck.confirmed_date}
                </span>
              ) : null}
              {factCheck.confidence ? (
                <span className="text-[10px] capitalize text-muted-foreground">
                  {factCheck.confidence} confidence
                </span>
              ) : null}
            </div>
            {factCheck.reasoning ? (
              <p className="mt-1.5 leading-snug text-foreground/80">{factCheck.reasoning}</p>
            ) : null}
          </div>
        </div>
      ) : null}

      {isPastDate(event.date) ? (
        <div>
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Result
          </p>
          <EventResultBlock
            predicted={event.articles[0]?.predicted_impact}
            actual={event.articles[0]?.actual_impact}
          />
        </div>
      ) : null}

      <div>
        <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          Source article{event.articles.length === 1 ? "" : "s"}
        </p>
        {event.articles.length ? (
          <ul className="space-y-1.5">
            {event.articles.map((article, idx) => (
              <li
                key={article.event_id || idx}
                className="rounded-md border bg-background/80 px-2.5 py-2 text-[12px]"
              >
                {article.url ? (
                  <a
                    href={article.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 font-medium text-foreground hover:underline"
                  >
                    {article.title || "Untitled article"}
                    <ExternalLink className="h-3 w-3 shrink-0" />
                  </a>
                ) : (
                  <span className="font-medium text-foreground">
                    {article.title || "Untitled article"}
                  </span>
                )}
                <div className="mt-1 flex flex-wrap items-center gap-1.5">
                  {article.publisher ? (
                    <span className="text-[10px] text-muted-foreground">{article.publisher}</span>
                  ) : null}
                  {article.verification_status ? (
                    <span className="text-[10px] capitalize text-muted-foreground">
                      {article.verification_status.replace(/_/g, " ")}
                    </span>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[12px] text-muted-foreground">No source article resolved for this event.</p>
        )}
      </div>
    </>
  );
}

function CalendarEventDetail({ event }: { event: IndexUpcomingEvent }) {
  return (
    <>
      <div>
        <h3 className="text-sm font-semibold leading-snug text-foreground">
          {event.label || event.event_type || "Calendar event"}
        </h3>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {event.date ? (
            <span className="text-[11px] tabular-nums text-muted-foreground">{event.date}</span>
          ) : null}
          {event.category ? (
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
              {event.category}
            </span>
          ) : null}
          {event.event_type ? (
            <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
              {event.event_type}
            </span>
          ) : null}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 text-[12px]">
        {event.symbol ? (
          <div className="rounded-md border bg-muted/10 px-2.5 py-2">
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground">Symbol</p>
            <p className="font-medium text-foreground">{event.symbol}</p>
          </div>
        ) : null}
        {event.sector ? (
          <div className="rounded-md border bg-muted/10 px-2.5 py-2">
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground">Sector</p>
            <p className="font-medium text-foreground">{event.sector}</p>
          </div>
        ) : null}
        {event.weight != null ? (
          <div className="rounded-md border bg-muted/10 px-2.5 py-2">
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground">Index weight</p>
            <p className="font-medium text-foreground">{(event.weight * 100).toFixed(2)}%</p>
          </div>
        ) : null}
        {event.impact ? (
          <div className="rounded-md border bg-muted/10 px-2.5 py-2">
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground">Impact</p>
            <p className="font-medium text-foreground">{event.impact}</p>
          </div>
        ) : null}
      </div>

      <p className="rounded-md border border-dashed bg-muted/10 px-2.5 py-2 text-[12px] text-muted-foreground">
        This is a structured calendar entry (macro/earnings schedule), not a distilled news item —
        no linked news-hub article or reconciled outcome is available for it.
      </p>
    </>
  );
}

export function EventDetailPanel({
  item,
  onClose,
}: {
  item: CalendarEventItem;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-[420px] flex-col border-l bg-background shadow-xl">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Event detail
          </p>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
          {item.kind === "news" ? <NewsEventDetail event={item.data} /> : <CalendarEventDetail event={item.data} />}
        </div>
      </aside>
    </div>
  );
}
