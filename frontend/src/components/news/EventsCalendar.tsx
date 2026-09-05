import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type HubNewsCalendarEvent } from "@/lib/api";
import { cn } from "@/lib/utils";
import { EventDetailPanel } from "@/components/news/EventDetailPanel";

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

function verificationDotClass(status?: string): string {
  const value = (status || "").toLowerCase();
  if (value === "calendar_corroborated") return "bg-emerald-500";
  if (value === "multi_source") return "bg-blue-500";
  return "bg-amber-500";
}

export function EventsCalendar() {
  const [selectedDate, setSelectedDate] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), now.getDate());
  });
  const [events, setEvents] = useState<HubNewsCalendarEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<HubNewsCalendarEvent | null>(null);

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
        setEvents(res.events || []);
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

  const eventsByDate = useMemo(() => {
    const map = new Map<string, HubNewsCalendarEvent[]>();
    const undated: HubNewsCalendarEvent[] = [];
    for (const event of events) {
      const parsed = parseEventDate(event.date);
      if (!parsed) {
        undated.push(event);
        continue;
      }
      const key = toIsoDate(parsed);
      const list = map.get(key) ?? [];
      list.push(event);
      map.set(key, list);
    }
    return { map, undated };
  }, [events]);

  const today = toIsoDate(new Date());
  const selectedKey = toIsoDate(selectedDate);
  const selectedDayEvents = eventsByDate.map.get(selectedKey) ?? [];

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
          const dayEvents = eventsByDate.map.get(key) ?? [];
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
              {dayEvents.length ? (
                <span className="flex items-center gap-0.5">
                  {dayEvents.slice(0, 3).map((event, idx) => (
                    <span
                      key={idx}
                      className={cn("h-1.5 w-1.5 rounded-full", verificationDotClass(event.verification_status))}
                    />
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
        {selectedDayEvents.length ? (
          selectedDayEvents.map((event, idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => setSelectedEvent(event)}
              className="flex w-full items-start gap-2 rounded-lg border bg-background px-2.5 py-2 text-left hover:bg-muted/40"
            >
              <span
                className={cn(
                  "mt-1 h-1.5 w-1.5 shrink-0 rounded-full",
                  verificationDotClass(event.verification_status),
                )}
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[12px] font-medium text-foreground">
                  {event.event || "Event"}
                </span>
                {event.type ? (
                  <span className="text-[10px] text-muted-foreground">{event.type}</span>
                ) : null}
              </span>
            </button>
          ))
        ) : (
          <p className="rounded-lg border border-dashed bg-muted/20 px-4 py-6 text-center text-[12px] text-muted-foreground">
            {loading ? "Loading events…" : "No extracted events for this date."}
          </p>
        )}
      </div>

      {eventsByDate.undated.length ? (
        <div className="mt-3">
          <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Undated events this week ({eventsByDate.undated.length})
          </p>
          <div className="flex flex-wrap gap-1.5">
            {eventsByDate.undated.map((event, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => setSelectedEvent(event)}
                className="flex items-center gap-1 rounded-full border bg-muted/10 px-2 py-1 text-[11px] hover:bg-muted/40"
              >
                <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", verificationDotClass(event.verification_status))} />
                {event.event || event.timeline_phrase || "Event"}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {selectedEvent ? (
        <EventDetailPanel event={selectedEvent} onClose={() => setSelectedEvent(null)} />
      ) : null}
    </div>
  );
}
