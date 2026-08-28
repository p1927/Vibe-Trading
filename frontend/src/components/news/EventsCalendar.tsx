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
  const [monthCursor, setMonthCursor] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });
  const [events, setEvents] = useState<HubNewsCalendarEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<HubNewsCalendarEvent | null>(null);

  const gridStart = useMemo(() => {
    const first = new Date(monthCursor.getFullYear(), monthCursor.getMonth(), 1);
    const start = new Date(first);
    start.setDate(first.getDate() - first.getDay());
    return start;
  }, [monthCursor]);

  const gridDays = useMemo(() => {
    return Array.from({ length: 42 }, (_, i) => {
      const d = new Date(gridStart);
      d.setDate(gridStart.getDate() + i);
      return d;
    });
  }, [gridStart]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const start = toIsoDate(gridDays[0]);
    const end = toIsoDate(gridDays[gridDays.length - 1]);
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
  }, [gridDays]);

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

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setMonthCursor((m) => new Date(m.getFullYear(), m.getMonth() - 1, 1))}
            className="rounded-md border p-1 hover:bg-muted/50"
            aria-label="Previous month"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>
          <p className="min-w-[140px] text-center text-[12px] font-medium">
            {MONTH_LABELS[monthCursor.getMonth()]} {monthCursor.getFullYear()}
          </p>
          <button
            type="button"
            onClick={() => setMonthCursor((m) => new Date(m.getFullYear(), m.getMonth() + 1, 1))}
            className="rounded-md border p-1 hover:bg-muted/50"
            aria-label="Next month"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
        </div>
        {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" /> : null}
      </div>

      {error ? (
        <p className="mb-2 rounded-md border border-dashed bg-muted/10 px-3 py-2 text-[12px] text-muted-foreground">
          {error}
        </p>
      ) : null}

      <div className="grid grid-cols-7 gap-px overflow-hidden rounded-lg border bg-border text-[11px]">
        {DOW_LABELS.map((label) => (
          <div key={label} className="bg-muted/40 px-1.5 py-1 text-center font-medium text-muted-foreground">
            {label}
          </div>
        ))}
        {gridDays.map((day) => {
          const key = toIsoDate(day);
          const inMonth = day.getMonth() === monthCursor.getMonth();
          const dayEvents = eventsByDate.map.get(key) ?? [];
          const visible = dayEvents.slice(0, 3);
          const overflow = dayEvents.length - visible.length;
          return (
            <div
              key={key}
              className={cn(
                "min-h-[84px] space-y-1 bg-background p-1",
                !inMonth && "bg-muted/10 text-muted-foreground",
                key === today && "ring-1 ring-inset ring-primary",
              )}
            >
              <p className={cn("px-0.5 text-[10px] tabular-nums", !inMonth && "opacity-50")}>{day.getDate()}</p>
              {visible.map((event, idx) => (
                <button
                  key={`${key}-${idx}`}
                  type="button"
                  onClick={() => setSelectedEvent(event)}
                  className="flex w-full items-center gap-1 rounded px-1 py-0.5 text-left hover:bg-muted/60"
                >
                  <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", verificationDotClass(event.verification_status))} />
                  <span className="truncate">{event.event || "Event"}</span>
                </button>
              ))}
              {overflow > 0 ? (
                <p className="px-1 text-[10px] text-muted-foreground">+{overflow} more</p>
              ) : null}
            </div>
          );
        })}
      </div>

      {eventsByDate.undated.length ? (
        <div className="mt-3">
          <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Undated events ({eventsByDate.undated.length})
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

      {!loading && !events.length && !error ? (
        <p className="mt-3 rounded-lg border border-dashed bg-muted/20 px-4 py-6 text-center text-[12px] text-muted-foreground">
          No extracted future events in this window yet.
        </p>
      ) : null}

      {selectedEvent ? (
        <EventDetailPanel event={selectedEvent} onClose={() => setSelectedEvent(null)} />
      ) : null}
    </div>
  );
}
