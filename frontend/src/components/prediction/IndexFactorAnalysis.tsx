import { IndexFactorChart } from "@/components/charts/IndexFactorChart";
import type { IndexPredictionArtifact } from "@/lib/api";

interface Props {
  artifact: IndexPredictionArtifact;
}

export function IndexFactorAnalysis({ artifact }: Props) {
  const sensitivity = (artifact.factor_sensitivity || []) as Parameters<
    typeof IndexFactorChart
  >[0]["sensitivity"];
  const eventCurves = (artifact.event_impact_curves || []) as Parameters<
    typeof IndexFactorChart
  >[0]["eventCurves"];

  if (!sensitivity.length && !eventCurves?.length) {
    return (
      <div className="rounded-xl border bg-card p-4 text-[12px] text-muted-foreground shadow-sm">
        Factor sensitivity curves not available — run analysis after macro model is trained.
      </div>
    );
  }

  const topContributor = artifact.factor_explanation?.contributors?.[0];

  return (
    <div className="space-y-3 rounded-xl border bg-card p-4 shadow-sm">
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
          Factor &amp; event sensitivity
        </p>
        <p className="mt-1 text-[11px] text-muted-foreground">
          Isolated ± shocks to each Ridge input vs index level at horizon. Event paths apply coordinated
          shocks (oil spike, FII outflow, RBI surprise).
        </p>
      </div>
      <IndexFactorChart
        sensitivity={sensitivity}
        eventCurves={eventCurves}
        spot={artifact.spot ?? undefined}
        height={260}
      />
      {topContributor ? (
        <p className="text-[11px] text-muted-foreground">
          Largest macro contributor today:{" "}
          <span className="font-medium text-foreground">
            {topContributor.label || topContributor.factor}
          </span>
          {typeof topContributor.contribution_pct === "number"
            ? ` (${topContributor.contribution_pct >= 0 ? "+" : ""}${topContributor.contribution_pct.toFixed(2)}%)`
            : ""}
        </p>
      ) : null}
      {artifact.factor_explanation?.contributors?.length ? (
        <details className="text-[11px]">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
            All macro contributors ({artifact.factor_explanation.contributors.length})
          </summary>
          <ul className="mt-2 max-h-40 space-y-0.5 overflow-y-auto">
            {artifact.factor_explanation.contributors.map((row) => (
              <li key={String(row.factor)}>
                <span className="font-medium">{row.label || row.factor}</span>
                {typeof row.contribution_pct === "number"
                  ? `: ${row.contribution_pct >= 0 ? "+" : ""}${row.contribution_pct.toFixed(2)}%`
                  : ""}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
