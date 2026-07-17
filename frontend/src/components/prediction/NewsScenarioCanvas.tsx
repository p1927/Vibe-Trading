import { useCallback, useEffect, useState } from "react";
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
  disabled?: boolean;
  dateRange?: NewsScenarioDateRange | null;
  selectedOutcomeId?: string | null;
  embeddedNewsItemCount?: number;
  recentScenarios?: Array<Record<string, unknown>>;
  onDateRangeChange?: (range: NewsScenarioDateRange) => void;
  onOutcomeSelect?: (outcomeId: string) => void;
  onLoadScenario?: (scenarioId: string) => void;
}

export function NewsScenarioCanvas({
  widget,
  className,
  disabled = false,
  dateRange,
  selectedOutcomeId,
  embeddedNewsItemCount,
  recentScenarios = [],
  onDateRangeChange,
  onOutcomeSelect,
  onLoadScenario,
}: Props) {
  const [startDate, setStartDate] = useState(dateRange?.start?.slice(0, 10) ?? "");
  const [endDate, setEndDate] = useState(dateRange?.end?.slice(0, 10) ?? "");

  useEffect(() => {
    setStartDate(dateRange?.start?.slice(0, 10) ?? widget?.date_range?.start?.slice(0, 10) ?? "");
    setEndDate(dateRange?.end?.slice(0, 10) ?? widget?.date_range?.end?.slice(0, 10) ?? "");
  }, [dateRange?.start, dateRange?.end, widget?.date_range?.start, widget?.date_range?.end]);

  const emitDateRange = useCallback(
    (start: string, end: string) => {
      if (!start || !end || !onDateRangeChange || disabled) return;
      onDateRangeChange({ start, end });
    },
    [disabled, onDateRangeChange],
  );

  const eventTitle =
    (widget?.event?.title as string | undefined)?.trim() ||
    (widget?.event?.headline as string | undefined)?.trim() ||
    "News event scenario";
  const dateRangeLabel = fmtRange(widget?.date_range ?? dateRange ?? undefined);
  const outcomes = widget?.outcomes ?? [];
  const activeOutcomeId = selectedOutcomeId ?? widget?.selected_outcome_id;

  return (
    <div
      className={cn(
        "flex h-full min-h-[420px] flex-col gap-4 rounded-xl border border-border/60 bg-card/40 p-4 shadow-sm",
        disabled && "pointer-events-none opacity-60",
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
        {dateRangeLabel && !onDateRangeChange ? (
          <span className="rounded-full border border-border/60 bg-muted/20 px-2.5 py-1 text-[10px] font-medium text-muted-foreground">
            {dateRangeLabel}
          </span>
        ) : null}
      </div>

      {onDateRangeChange ? (
        <div className="flex flex-wrap items-end gap-2 rounded-lg border border-border/50 bg-muted/10 p-2">
          <label className="flex flex-col gap-0.5 text-[10px] text-muted-foreground">
            From
            <input
              type="date"
              value={startDate}
              disabled={disabled}
              onChange={(e) => {
                const v = e.target.value;
                setStartDate(v);
                emitDateRange(v, endDate);
              }}
              className="rounded-md border border-border/60 bg-background px-2 py-1 text-xs text-foreground"
            />
          </label>
          <label className="flex flex-col gap-0.5 text-[10px] text-muted-foreground">
            To
            <input
              type="date"
              value={endDate}
              disabled={disabled}
              onChange={(e) => {
                const v = e.target.value;
                setEndDate(v);
                emitDateRange(startDate, v);
              }}
              className="rounded-md border border-border/60 bg-background px-2 py-1 text-xs text-foreground"
            />
          </label>
          <p className="pb-1 text-[10px] text-muted-foreground">Max 90 days · synced to advisor session</p>
        </div>
      ) : null}

      {embeddedNewsItemCount === 0 ? (
        <p className="rounded-md border border-border/50 bg-muted/10 px-2 py-1.5 text-[10px] text-muted-foreground">
          No embedded headlines in this Analysis snapshot — you can still define custom news events in
          chat.
        </p>
      ) : null}

      {!widget ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border/60 bg-muted/10 px-4 text-center">
          <p className="text-sm font-medium text-foreground">No scenario chart yet</p>
          <p className="max-w-sm text-[11px] text-muted-foreground">
            Set a date range, describe a news event and outcomes in the advisor chat. When the agent
            runs the quant pipeline, paths appear here.
          </p>
        </div>
      ) : (
        <>
          <NewsScenarioPathChart
            baselinePath={widget.baseline?.path}
            outcomes={outcomes}
            fanBand={widget.fan_band}
            selectedOutcomeId={activeOutcomeId}
            height={320}
          />

          {outcomes.length > 0 ? (
            <div>
              <p className="mb-2 text-[11px] font-medium text-muted-foreground">Outcome branches</p>
              <div className="flex flex-wrap gap-2">
                {outcomes.map((outcome) => {
                  const id = outcome.id || outcome.label || "";
                  const active = Boolean(activeOutcomeId && id === activeOutcomeId);
                  return (
                    <button
                      key={id}
                      type="button"
                      disabled={disabled || !id || !onOutcomeSelect}
                      onClick={() => id && onOutcomeSelect?.(id)}
                      className={cn(
                        "rounded-lg border px-3 py-2 text-left text-xs transition-colors",
                        active
                          ? "border-primary/50 bg-primary/5"
                          : "border-border/50 bg-muted/10 opacity-80 hover:opacity-100",
                        onOutcomeSelect && id && "cursor-pointer",
                      )}
                    >
                      <div className="font-semibold text-foreground">
                        {outcome.label || outcome.id}
                      </div>
                      <div className="mt-0.5 font-mono text-[10px] text-primary">
                        {formatOutcomeReturn(outcome)}
                        {outcome.probability_hint ? (
                          <span className="text-muted-foreground">
                            {" "}
                            · agent estimate {outcome.probability_hint}
                          </span>
                        ) : null}
                        {outcome.intensity ? ` · ${outcome.intensity}` : ""}
                      </div>
                      {outcome.range?.low != null && outcome.range?.high != null ? (
                        <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                          {Number(outcome.range.low).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                          {" – "}
                          {Number(outcome.range.high).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                        </div>
                      ) : null}
                    </button>
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
              {widget.baseline.equation_ref ? (
                <>
                  {" "}
                  (bottom-up {widget.baseline.equation_ref.bottom_up ?? "—"}% + macro{" "}
                  {widget.baseline.equation_ref.macro_delta ?? "—"}%)
                </>
              ) : null}
            </p>
          ) : null}

          {recentScenarios.length > 0 && onLoadScenario ? (
            <div>
              <p className="mb-2 text-[11px] font-medium text-muted-foreground">Recent scenarios</p>
              <div className="flex flex-wrap gap-1.5">
                {recentScenarios.slice(0, 6).map((row) => {
                  const id = String(row.scenario_id ?? "");
                  const title =
                    String((row.event as { title?: string } | undefined)?.title ?? id).slice(0, 48);
                  return (
                    <button
                      key={id}
                      type="button"
                      disabled={disabled || !id}
                      onClick={() => onLoadScenario(id)}
                      className="rounded-md border border-border/50 bg-muted/10 px-2 py-1 text-[10px] hover:bg-muted/20"
                    >
                      {title || id}
                    </button>
                  );
                })}
              </div>
            </div>
          ) : null}
        </>
      )}

      <footer className="mt-auto border-t border-border/40 pt-3 text-[10px] leading-relaxed text-muted-foreground">
        Scenario paths use the same Ridge + bottom-up model as Analysis, applied to your selected date
        range. Not a guarantee of future prices. Probability hints are agent estimates — not
        model-calibrated.
      </footer>
    </div>
  );
}
