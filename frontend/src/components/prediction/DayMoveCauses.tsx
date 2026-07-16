import { cn } from "@/lib/utils";
import type { CausalHypothesis, IndexDayAttribution } from "@/lib/api";

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

const CATEGORY_LABEL: Record<string, string> = {
  commodity: "Commodity",
  flows: "Institutional flows",
  global: "Global markets",
  rates: "Rates",
  risk: "Risk / VIX",
  derivatives: "Derivatives",
  sentiment: "Sentiment",
  policy: "Policy",
  calendar: "Calendar",
  news: "News",
  company: "Company",
  factor: "Factor",
  composite: "Composite",
};

interface Props {
  attribution?: IndexDayAttribution | null;
  causalHypotheses?: CausalHypothesis[];
  compact?: boolean;
}

export function DayMoveCauses({ attribution, causalHypotheses, compact }: Props) {
  const causes = causalHypotheses ?? attribution?.causal_hypotheses ?? [];
  const headlines = attribution?.index_headlines ?? [];

  if (!causes.length && !headlines.length) {
    return (
      <p className="text-[11px] text-muted-foreground">
        No causal narrative yet — run company news backfill for headline replay, or pick a day with larger
        factor moves.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div>
        <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          Likely causes (ranked)
        </p>
        <p className="mb-2 text-[10px] text-muted-foreground">
          Hypotheses link factor moves, flows, and headlines — not post-hoc forecast errors.
        </p>
        <ol className="space-y-2">
          {causes.map((h, i) => (
            <li
              key={`${h.title}-${i}`}
              className={cn(
                "rounded-lg border bg-background/60 p-2.5",
                i === 0 && "border-primary/30 ring-1 ring-primary/10",
              )}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <p className="text-[11px] font-medium leading-snug">{h.title}</p>
                <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-muted-foreground">
                  {CATEGORY_LABEL[h.category ?? ""] ?? h.category ?? "cause"}
                </span>
              </div>
              {h.explanation ? (
                <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{h.explanation}</p>
              ) : null}
              {h.evidence?.length ? (
                <ul className="mt-1.5 space-y-0.5 text-[10px] text-muted-foreground">
                  {h.evidence.slice(0, 2).map((e) => (
                    <li key={e}>↳ {e}</li>
                  ))}
                </ul>
              ) : null}
              {h.confidence != null && !compact ? (
                <p className="mt-1 text-[9px] tabular-nums text-muted-foreground">
                  Confidence {Math.round(h.confidence * 100)}%
                </p>
              ) : null}
            </li>
          ))}
        </ol>
      </div>

      {headlines.length && !compact ? (
        <div>
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Index headlines that day
          </p>
          <ul className="space-y-1 text-[11px]">
            {headlines.map((h) => (
              <li key={h.title} className="text-muted-foreground">
                • {h.title}
                {h.source ? <span className="ml-1 text-[10px] opacity-70">({h.source})</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {!compact && attribution?.factor_drivers?.length ? (
        <details className="text-[11px]">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
            Raw factor moves (d/d)
          </summary>
          <ul className="mt-1 space-y-0.5 pl-2">
            {attribution.factor_drivers.map((d) => (
              <li key={d.factor}>
                {d.label ?? d.factor}: {d.prev} → {d.current} ({fmtPct(d.change_pct)} d/d)
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
