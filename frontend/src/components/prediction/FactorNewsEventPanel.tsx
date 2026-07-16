import { cn } from "@/lib/utils";
import type { PlaygroundTrigger } from "@/lib/api";
import { factorLabel } from "@/lib/factorEventMapping";

interface Props {
  headlines: PlaygroundTrigger[];
  events: PlaygroundTrigger[];
  selectedId: string | null;
  onSelect: (trigger: PlaygroundTrigger) => void;
  loading?: boolean;
}

function TriggerRow({
  item,
  selected,
  onClick,
}: {
  item: PlaygroundTrigger;
  selected: boolean;
  onClick: () => void;
}) {
  const label = item.title || item.label || "Event";
  const factor = item.primary_factor ? factorLabel(item.primary_factor) : null;
  const prob =
    item.probability != null && Number.isFinite(item.probability)
      ? `${Math.round(item.probability * 100)}% likely`
      : null;

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "w-full rounded-lg border px-2.5 py-2 text-left transition-colors",
        selected ? "border-primary/50 bg-primary/10" : "border-border/60 hover:bg-muted/40",
      )}
    >
      <p className="text-[11px] font-medium leading-snug">{label}</p>
      <p className="mt-0.5 text-[10px] text-muted-foreground">
        {item.kind === "headline" || item.kind === "material" ? "News" : item.kind ?? "Event"}
        {factor ? ` · drives ${factor}` : ""}
        {item.days_from_now != null ? ` · D+${item.days_from_now}` : ""}
        {prob ? ` · ${prob}` : ""}
      </p>
      {item.why ? <p className="mt-1 text-[10px] text-muted-foreground line-clamp-2">{item.why}</p> : null}
    </button>
  );
}

export function FactorNewsEventPanel({
  headlines,
  events,
  selectedId,
  onSelect,
  loading,
}: Props) {
  if (loading) {
    return <p className="text-[11px] text-muted-foreground">Loading news & events…</p>;
  }

  return (
    <div className="space-y-3">
      <div>
        <p className="text-[9px] font-semibold uppercase tracking-wide text-muted-foreground">
          Recent headlines
        </p>
        <div className="mt-1.5 max-h-[200px] space-y-1.5 overflow-y-auto">
          {headlines.length ? (
            headlines.map((h, i) => (
              <TriggerRow
                key={h.id || `h-${i}`}
                item={h}
                selected={selectedId === (h.id || `h-${i}`)}
                onClick={() => onSelect({ ...h, id: h.id || `h-${i}` })}
              />
            ))
          ) : (
            <p className="text-[10px] text-muted-foreground">No headlines fetched for today.</p>
          )}
        </div>
      </div>

      <div>
        <p className="text-[9px] font-semibold uppercase tracking-wide text-muted-foreground">
          Events & scenarios
        </p>
        <div className="mt-1.5 max-h-[200px] space-y-1.5 overflow-y-auto">
          {events.length ? (
            events.map((e, i) => (
              <TriggerRow
                key={e.id || `e-${i}`}
                item={e}
                selected={selectedId === (e.id || `e-${i}`)}
                onClick={() => onSelect({ ...e, id: e.id || `e-${i}` })}
              />
            ))
          ) : (
            <p className="text-[10px] text-muted-foreground">No upcoming events in horizon.</p>
          )}
        </div>
      </div>
    </div>
  );
}
