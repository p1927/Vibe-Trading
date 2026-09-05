import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type HistoricalUpcomingEvent, type HubNewsCalendarEvent, type IndexUpcomingEvent } from "@/lib/api";
import { cn } from "@/lib/utils";
import { EventDetailPanel, type CalendarEventItem } from "@/components/news/EventDetailPanel";

const DOW_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH_LABELS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

function toIsoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function parseEventDate(value?: string): Date | null {
  if (!value) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value.trim());
  if (!match) return null;
  const d = new Date(parseInt(match[1], 10), parseInt(match[2], 10) - 1, parseInt(match[3], 10));
  return Number.isNaN(d.getTime()) ? null : d;
}

function itemLabel(item: CalendarEventItem): string {
  return item.kind === "news" ? item.data.event || "Event" : item.data.label || item.data.event_type || "Event";
}

function itemSubLabel(item: CalendarEventItem): string | null {
  return item.kind === "news" ? item.data.type || null : item.data.category || item.data.event_type || null;
}

function dotClass(item: CalendarEventItem): string {
  if (item.kind === "calendar") return "bg-sky-500";
  const value = (item.data.verification_status || "").toLowerCase();
  if (value === "calendar_corroborated") return "bg-emerald-500";
  if (value === "multi_source") return "bg-blue-500";
  return "bg-amber-500";
}

export function EventsCalendar({
  structuralEvents = [],
  isNewStructuralEvent,
  includeStructuralHistory = false,
}: {
  structuralEvents?: IndexUpcomingEvent[];
  isNewStructuralEvent?: (event: IndexUpcomingEvent) => boolean;
  /** Also self-fetch past structural (RBI/FOMC/earnings/macro) events reconstructed from the
   * index_research snapshot archive, each carrying a realized market-outcome when matured. Off
   * by default so callers that only pass forward-looking `structuralEvents` (or none at all,
   * like the Hub's news-only calendar) don't unexpectedly gain a second data source. */
  includeStructuralHistory?: boolean;
}) {
  const [selectedDate, setSelectedDate] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), now.getDate());
  });
  const [newsEvents, setNewsEvents] = useState<HubNewsCalendarEvent[]>([]);
  const [historicalStructuralEvents, setHistoricalStructuralEvents] = useState<HistoricalUpcomingEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedItem, setSelectedItem] = useState<CalendarEventItem | null>(null);

  const weekStart = useMemo(() => {
    const start = new Date(selectedDate);
    start.setDate(selectedDate.getDate() - selectedDate.getDay());
    return start;
  }, [selectedDate]);

  const weekDays = useMemo(() => {
    return Array.from({ length: 7 }, (_, i) => {
      const d = new Date(weekStart);
      d.setDate(weekStart.getDate() + i);
      return d;
    });
  }, [weekStart]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const start = toIsoDate(weekDays[0]);
    const end = toIsoDate(weekDays[weekDays.length - 1]);
    api
      .getHubNewsEventsCalendar({ start, end })
      .then((res) => {
        if (cancelled) return;
        setNewsEvents(res.events || []);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load events");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [weekDays]);

  useEffect(() => {
    if (!includeStructuralHistory) {
      setHistoricalStructuralEvents([]);
      return;
    }
    let cancelled = false;
    const start = toIsoDate(weekDays[0]);
    const end = toIsoDate(weekDays[weekDays.length - 1]);
    api
      .getIndexPredictionUpcomingEventsHistory({ start, end })
      .then((res) => {
        if (cancelled) return;
        setHistoricalStructuralEvents(res.events || []);
      })
      .catch(() => {
        /* supplementary past-events source — the live forward-looking structuralEvents
         * prop already renders fine without it, so fail silently here. */
      });
    return () => {
      cancelled = true;
    };
  }, [weekDays, includeStructuralHistory]);

  const allItems = useMemo<CalendarEventItem[]>(() => {
    const news: CalendarEventItem[] = newsEvents.map((data, idx) => ({
      kind: "news",
      key: `news-${data.date || "undated"}-${idx}`,
      data,
    }));

    // Merge live forward-looking structural events with the archive-reconstructed historical
    // ones, deduped by (date, label/event_type) — prefer the historical copy when both exist
    // since only it carries a computed `market_outcome`.
    const structuralByKey = new Map<string, IndexUpcomingEvent>();
    for (const ev of structuralEvents) {
      structuralByKey.set(`${ev.date || ""}|${ev.label || ev.event_type || ""}`, ev);
    }
    for (const ev of historicalStructuralEvents) {
      structuralByKey.set(`${ev.date || ""}|${ev.label || ev.event_type || ""}`, ev);
    }

    const structural: CalendarEventItem[] = Array.from(structuralByKey.entries()).map(([key, data]) => ({
      kind: "calendar",
      key: `cal-${key}`,
      data,
    }));
    return [...news, ...structural];
  }, [newsEvents, structuralEvents, historicalStructuralEvents]);

  const eventsByDate = useMemo(() => {
    const map = new Map<string, CalendarEventItem[]>();
    const undated: CalendarEventItem[] = [];
    for (const item of allItems) {
      const rawDate = item.kind === "news" ? item.data.date : item.data.date;
      const parsed = parseEventDate(rawDate);
      if (!parsed) {
        undated.push(item);
        continue;
      }
      const key = toIsoDate(parsed);
      const list = map.get(key) ?? [];
      list.push(item);
      map.set(key, list);
    }
    return { map, undated };
  }, [allItems]);

  const today = toIsoDate(new Date());
  const selectedKey = toIsoDate(selectedDate);
  const selectedDayItems = eventsByDate.map.get(selectedKey) ?? [];

  const goToDay = (deltaDays: number) => {
    setSelectedDate((d) => {
      const next = new Date(d);
      next.setDate(d.getDate() + deltaDays);
      return next;
    });
  };

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => goToDay(-1)}
            className="rounded-md border p-1 hover:bg-muted/50"
            aria-label="Previous day"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>
          <p className="min-w-[170px] text-center text-[12px] font-medium">
            {DOW_LABELS[selectedDate.getDay()]}, {MONTH_LABELS[selectedDate.getMonth()]} {selectedDate.getDate()}{" "}
            {selectedDate.getFullYear()}
          </p>
          <button
            type="button"
            onClick={() => goToDay(1)}
            className="rounded-md border p-1 hover:bg-muted/50"
            aria-label="Next day"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
          {selectedKey !== today ? (
            <button
              type="button"
              onClick={() => setSelectedDate(new Date())}
              className="ml-1 rounded-md border px-1.5 py-1 text-[10px] text-muted-foreground hover:bg-muted/50"
            >
              Today
            </button>
          ) : null}
        </div>
        {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" /> : null}
      </div>

      {error ? (
        <p className="mb-2 rounded-md border border-dashed bg-muted/10 px-3 py-2 text-[12px] text-muted-foreground">
          {error}
        </p>
      ) : null}

      <div className="grid grid-cols-7 gap-1.5">
        {weekDays.map((day) => {
          const key = toIsoDate(day);
          const dayItems = eventsByDate.map.get(key) ?? [];
          const isSelected = key === selectedKey;
          return (
            <button
              key={key}
              type="button"
              onClick={() => setSelectedDate(day)}
              className={cn(
                "flex flex-col items-center gap-1 rounded-lg border px-1 py-1.5 text-center hover:bg-muted/50",
                isSelected && "border-primary bg-primary/10",
                key === today && !isSelected && "ring-1 ring-inset ring-primary/50",
              )}
            >
              <span className="text-[9px] uppercase tracking-wide text-muted-foreground">
                {DOW_LABELS[day.getDay()]}
              </span>
              <span className={cn("text-[13px] tabular-nums font-medium", isSelected && "text-primary")}>
                {day.getDate()}
              </span>
              {dayItems.length ? (
                <span className="flex items-center gap-0.5">
                  {dayItems.slice(0, 3).map((item) => (
                    <span key={item.key} className={cn("h-1.5 w-1.5 rounded-full", dotClass(item))} />
                  ))}
                </span>
              ) : (
                <span className="h-1.5" />
              )}
            </button>
          );
        })}
      </div>

      <div className="mt-3 space-y-1.5">
        {selectedDayItems.length ? (
          selectedDayItems.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setSelectedItem(item)}
              className="flex w-full items-start gap-2 rounded-lg border bg-background px-2.5 py-2 text-left hover:bg-muted/40"
            >
              <span className={cn("mt-1 h-1.5 w-1.5 shrink-0 rounded-full", dotClass(item))} />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5">
                  <span className="block truncate text-[12px] font-medium text-foreground">{itemLabel(item)}</span>
                  {item.kind === "calendar" && isNewStructuralEvent?.(item.data) ? (
                    <span className="shrink-0 rounded bg-sky-500/20 px-1 text-[9px] font-semibold uppercase tracking-wide text-sky-700 dark:text-sky-400">
                      New
                    </span>
                  ) : null}
                </span>
                {itemSubLabel(item) ? (
                  <span className="text-[10px] text-muted-foreground">{itemSubLabel(item)}</span>
                ) : null}
              </span>
            </button>
          ))
        ) : (
          <p className="rounded-lg border border-dashed bg-muted/20 px-4 py-6 text-center text-[12px] text-muted-foreground">
            {loading ? "Loading events…" : "No events for this date."}
          </p>
        )}
      </div>

      {eventsByDate.undated.length ? (
        <div className="mt-3">
          <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Undated events this week ({eventsByDate.undated.length})
          </p>
          <div className="flex flex-wrap gap-1.5">
            {eventsByDate.undated.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => setSelectedItem(item)}
                className="flex items-center gap-1 rounded-full border bg-muted/10 px-2 py-1 text-[11px] hover:bg-muted/40"
              >
                <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", dotClass(item))} />
                {itemLabel(item)}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {selectedItem ? <EventDetailPanel item={selectedItem} onClose={() => setSelectedItem(null)} /> : null}
    </div>
  );
}
