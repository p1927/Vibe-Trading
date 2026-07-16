import { cn } from "@/lib/utils";

interface Props {
  interpretation?: {
    technical_interpretation?: string;
    active_strategy_profile?: string;
    strategy_rationale?: string;
    strategy_options_handoff?: string;
    strategy_risks?: string;
    strategy_context?: string;
    technical_readings?: Record<string, number>;
    horizon_name?: string;
  } | null;
  horizonDays?: number;
}

export function TechnicalContextStrip({ interpretation, horizonDays }: Props) {
  if (!interpretation?.technical_interpretation) return null;

  const readings = interpretation.technical_readings ?? {};
  const profile = interpretation.active_strategy_profile;

  return (
    <div className="rounded-xl border border-dashed border-primary/30 bg-primary/5 p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">Technical context</h3>
        {horizonDays != null ? (
          <span className="rounded-md bg-muted px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {horizonDays}d horizon
          </span>
        ) : null}
        {profile ? (
          <span className="rounded-md bg-primary/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-primary">
            {profile.replace(/_/g, " ")}
          </span>
        ) : null}
      </div>
      <p className="text-[12px] leading-relaxed text-muted-foreground">
        {interpretation.technical_interpretation}
      </p>
      {interpretation.strategy_options_handoff ? (
        <p className="mt-2 text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground">Options handoff: </span>
          {interpretation.strategy_options_handoff}
        </p>
      ) : null}
      {interpretation.strategy_risks ? (
        <p className="mt-1 text-[11px] text-amber-800 dark:text-amber-300">
          <span className="font-medium">Risks: </span>
          {interpretation.strategy_risks}
        </p>
      ) : null}
      {Object.keys(readings).length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {Object.entries(readings).map(([key, value]) => (
            <span
              key={key}
              className={cn(
                "rounded-md border bg-background px-2 py-1 text-[10px] tabular-nums",
                "text-muted-foreground"
              )}
            >
              {key.replace(/^nifty_/, "").replace(/_/g, " ")}:{" "}
              <span className="font-medium text-foreground">
                {typeof value === "number" ? value.toFixed(2) : value}
              </span>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
