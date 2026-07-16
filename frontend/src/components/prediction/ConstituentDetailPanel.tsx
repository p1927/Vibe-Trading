import type { ConstituentFactor, ConstituentSignal } from "@/lib/api";
import { displayText } from "@/lib/displayText";
import { ConstituentHistoryPanel } from "@/components/prediction/ConstituentHistoryPanel";

const TYPE_LABELS: Record<string, string> = {
  news_sentiment: "Sentiment",
  earnings: "Earnings",
  calendar: "Calendar",
  news: "News",
  macro: "Macro",
};

interface Props {
  signal: ConstituentSignal;
}

function renderEvents(signal: ConstituentSignal) {
  const events = signal.events ?? [];
  if (!events.length) return null;
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        Upcoming events ({events.length})
      </p>
      <ul className="mt-1 space-y-1">
        {events.slice(0, 6).map((event, i) => {
          const title = displayText(event.purpose || event.description || event.type);
          const date = displayText(event.date);
          return (
            <li key={`${date}-${i}`} className="rounded-md border bg-background/50 px-2 py-1">
              <span className="font-medium">{displayText(event.type) || "Event"}</span>
              {title ? <span className="text-muted-foreground"> — {title}</span> : null}
              {date ? <p className="text-[10px] text-muted-foreground">{date}</p> : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function ConstituentDetailPanel({ signal }: Props) {
  const factors = signal.factors ?? [];
  const weightPct =
    signal.weight != null ? (signal.weight <= 1 ? signal.weight * 100 : signal.weight) : null;
  const weightRaw = signal.weight != null ? (signal.weight <= 1 ? signal.weight : signal.weight / 100) : undefined;

  return (
    <div className="space-y-4 text-[11px]">
      <div className="grid gap-2 sm:grid-cols-3">
        <div>
          <span className="text-muted-foreground">Weight</span>
          <p className="font-medium tabular-nums">{weightPct != null ? `${weightPct.toFixed(2)}%` : "—"}</p>
        </div>
        <div>
          <span className="text-muted-foreground">Sentiment</span>
          <p className="font-medium tabular-nums">
            {signal.sentiment_score != null ? signal.sentiment_score.toFixed(3) : "—"}
          </p>
        </div>
        <div>
          <span className="text-muted-foreground">Index contribution</span>
          <p className="font-medium tabular-nums">
            {signal.contribution_to_index_pct != null
              ? `${signal.contribution_to_index_pct >= 0 ? "+" : ""}${signal.contribution_to_index_pct.toFixed(3)}%`
              : "—"}
          </p>
        </div>
      </div>

      <div>
        <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          Sentiment & contribution trend
        </p>
        <ConstituentHistoryPanel symbol={signal.symbol || ""} weight={weightRaw} days={365} />
      </div>

      {renderEvents(signal)}

      {factors.length > 0 ? (
        <>
          {factors.some((f) => f.type === "macro" || f.macro_link) ? (
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                Macro factors that affect this stock
              </p>
              <ul className="mt-1 space-y-1">
                {factors
                  .filter((f) => f.type === "macro" || f.macro_link)
                  .map((f, i) => (
                    <li key={`macro-${i}`} className="rounded-md border bg-background/50 px-2 py-1">
                      <span className="font-medium">{displayText(f.factor || f.macro_link)}</span>
                      {f.note ? <span className="text-muted-foreground"> — {displayText(f.note)}</span> : null}
                    </li>
                  ))}
              </ul>
            </div>
          ) : null}
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">All drivers</p>
            <ul className="mt-1 space-y-1.5">
            {factors.map((f: ConstituentFactor, i) => {
              const typeLabel = TYPE_LABELS[f.type || ""] || displayText(f.type) || "Driver";
              const factorName = displayText(f.factor || f.event || f.macro_link);
              const headline = displayText(f.headline);
              const note = displayText(f.note);
              const date = displayText(f.date);
              const impact = displayText(f.impact);
              return (
                <li key={`${f.type}-${factorName}-${i}`} className="rounded-md border bg-background/50 px-2 py-1.5">
                  <span className="font-medium text-foreground">{typeLabel}</span>
                  {factorName ? <span className="text-muted-foreground"> · {factorName}</span> : null}
                  {impact ? <span className="ml-1 text-muted-foreground">({impact})</span> : null}
                  {headline ? <p className="mt-0.5 text-muted-foreground">{headline}</p> : null}
                  {note ? <p className="mt-0.5 text-muted-foreground">{note}</p> : null}
                  {date ? <p className="text-[10px] text-muted-foreground">{date}</p> : null}
                  {f.score != null ? (
                    <p className="text-[10px] tabular-nums">score {Number(f.score).toFixed(3)}</p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
        </>
      ) : (
        <p className="text-muted-foreground">No structured drivers — refresh company research for this symbol.</p>
      )}
    </div>
  );
}
