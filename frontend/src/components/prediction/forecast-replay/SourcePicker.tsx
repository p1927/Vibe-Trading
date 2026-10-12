import { cn } from "@/lib/utils";
import type { SourceStyle } from "@/lib/forecastReplay/sourceStyle";
import type { Provenance, ReplaySource } from "@/lib/forecastReplay/types";
import { groupByFamily, sourceNote } from "@/lib/forecastReplay/view";

const PROVENANCES: Provenance[] = ["live", "replay", "backtest"];

interface Props {
  sources: ReplaySource[];
  /** Selected source ids; at least one stays selected (the toggle handler refuses to empty it). */
  selected: string[];
  provenance: Provenance[];
  styles: Record<string, SourceStyle>;
  single?: boolean;
  onToggleSource: (id: string) => void;
  onToggleProvenance: (p: Provenance) => void;
}

/** Sources grouped by family with a status chip each; only sources of the active provenances are listed. */
export function SourcePicker({ sources, selected, provenance, styles, single, onToggleSource, onToggleProvenance }: Props) {
  const visible = sources.filter((s) => provenance.includes(s.provenance));
  return (
    <div className="space-y-2 rounded-lg border border-border/50 bg-muted/20 p-2.5 text-[10px]" data-testid="source-picker">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold uppercase tracking-wide text-muted-foreground">Provenance</span>
        {PROVENANCES.map((p) => (
          <label key={p} className="inline-flex items-center gap-1">
            <input type="checkbox" checked={provenance.includes(p)} onChange={() => onToggleProvenance(p)} aria-label={`provenance ${p}`} />
            {p}
          </label>
        ))}
        <span className="text-muted-foreground">{single ? "pick one source" : `${selected.length} selected`}</span>
      </div>
      {groupByFamily(visible).map(({ family, sources: list }) => (
        <div key={family} className="space-y-1">
          <p className="font-medium text-muted-foreground">{family}</p>
          <div className="flex max-h-28 flex-wrap gap-1.5 overflow-y-auto">
            {list.map((s) => {
              const note = sourceNote(s);
              const on = selected.includes(s.source_id);
              const color = styles[s.source_id]?.color;
              return (
                <button
                  key={s.source_id}
                  type="button"
                  disabled={s.status !== "ok"}
                  aria-pressed={on}
                  title={note ?? `${s.label}: ${s.n_records} records ${s.first_day ?? ""} to ${s.last_day ?? ""}`}
                  onClick={() => onToggleSource(s.source_id)}
                  className={cn(
                    "inline-flex max-w-[16rem] items-center gap-1 rounded-full border px-2 py-0.5 font-medium transition-colors",
                    on ? "bg-primary/10 text-primary" : "border-border/60 text-muted-foreground hover:bg-muted/50",
                    s.status !== "ok" && "cursor-not-allowed opacity-60",
                  )}
                  style={on && color ? { borderColor: color } : undefined}
                >
                  <span className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: color }} />
                  <span className="truncate">{s.label}</span>
                  <span
                    data-status={s.status}
                    className={cn(
                      "min-w-0 truncate rounded px-1 text-[9px]",
                      s.status === "ok" ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : "bg-amber-500/10 text-amber-700 dark:text-amber-400",
                    )}
                  >
                    {s.status === "ok" ? `ok · ${s.n_records}` : note}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      ))}
      {!visible.length ? <p className="text-muted-foreground">No sources for the chosen provenance.</p> : null}
    </div>
  );
}
