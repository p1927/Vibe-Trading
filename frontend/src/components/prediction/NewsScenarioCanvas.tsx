import { Newspaper } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  NewsScenarioPathChart,
  formatOutcomeReturn,
} from "@/components/charts/NewsScenarioPathChart";
import type { NewsScenarioDateRange, TradePlanWidget } from "@/lib/api";

function fmtDate(iso: string | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

function fmtRange(range: NewsScenarioDateRange | undefined): string | null {
  if (!range?.start && !range?.end) return null;
  if (range.start && range.end) return `${fmtDate(range.start)} – ${fmtDate(range.end)}`;
  return fmtDate(range.start || range.end);
}

interface Props {
  widget: TradePlanWidget | null;
  className?: string;
}

export function NewsScenarioCanvas({ widget, className }: Props) {
  const eventTitle =
    (widget?.event?.title as string | undefined)?.trim() ||
    (widget?.event?.headline as string | undefined)?.trim() ||
    "News event scenario";
  const dateRangeLabel = fmtRange(widget?.date_range);
  const outcomes = widget?.outcomes ?? [];
  const selectedId = widget?.selected_outcome_id;

  return (
    <div
      className={cn(
        "flex h-full min-h-[420px] flex-col gap-4 rounded-xl border border-border/60 bg-card/40 p-4 shadow-sm",
        className,
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Newspaper className="h-4 w-4 text-primary" />
            News scenario — {widget?.underlying ?? "NIFTY"}
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground line-clamp-2">{eventTitle}</p>
        </div>
        {dateRangeLabel ? (
          <span className="rounded-full border border-border/60 bg-muted/20 px-2.5 py-1 text-[10px] font-medium text-muted-foreground">
            {dateRangeLabel}
          </span>
        ) : null}
      </div>

      {!widget ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border/60 bg-muted/10 px-4 text-center">
          <p className="text-sm font-medium text-foreground">No scenario chart yet</p>
          <p className="max-w-sm text-[11px] text-muted-foreground">
            Describe a news event and outcomes in the advisor chat. When the agent runs the quant
            pipeline, paths appear here.
          </p>
        </div>
      ) : (
        <>
          <NewsScenarioPathChart
            baselinePath={widget.baseline?.path}
            outcomes={outcomes}
            fanBand={widget.fan_band}
            selectedOutcomeId={selectedId}
            height={320}
          />

          {outcomes.length > 0 ? (
            <div>
              <p className="mb-2 text-[11px] font-medium text-muted-foreground">Outcome branches</p>
              <div className="flex flex-wrap gap-2">
                {outcomes.map((outcome) => {
                  const active = !selectedId || outcome.id === selectedId;
                  return (
                    <div
                      key={outcome.id || outcome.label}
                      className={cn(
                        "rounded-lg border px-3 py-2 text-left text-xs transition-colors",
                        active
                          ? "border-primary/50 bg-primary/5"
                          : "border-border/50 bg-muted/10 opacity-80",
                      )}
                    >
                      <div className="font-semibold text-foreground">
                        {outcome.label || outcome.id}
                      </div>
                      <div className="mt-0.5 font-mono text-[10px] text-primary">
                        {formatOutcomeReturn(outcome)}
                        {outcome.probability_hint ? ` · ${outcome.probability_hint}` : ""}
                        {outcome.intensity ? ` · ${outcome.intensity}` : ""}
                      </div>
                      {outcome.range?.low != null && outcome.range?.high != null ? (
                        <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                          {Number(outcome.range.low).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                          {" – "}
                          {Number(outcome.range.high).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}

          {widget.baseline?.expected_return_pct != null ? (
            <p className="text-[11px] text-muted-foreground">
              Baseline forecast{" "}
              <span className="font-mono text-foreground">
                {formatOutcomeReturn({ expected_return_pct: widget.baseline.expected_return_pct })}
              </span>
              {widget.baseline.spot != null ? (
                <>
                  {" "}
                  from spot{" "}
                  <span className="font-mono">
                    {Number(widget.baseline.spot).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                  </span>
                </>
              ) : null}
            </p>
          ) : null}
        </>
      )}

      <footer className="mt-auto border-t border-border/40 pt-3 text-[10px] leading-relaxed text-muted-foreground">
        Scenario paths use the frozen Analysis pipeline snapshot and factor shocks — not live
        market data. For research and planning only; not investment advice.
      </footer>
    </div>
  );
}
