import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, CheckCircle2, AlertTriangle, XCircle, MinusCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  MODEL_ROLE_LABELS,
  runPredictionUiAudit,
  type PredictionUiExternalState,
  type VerificationStatus,
} from "@/lib/predictionVerification";
import type { IndexPredictionArtifact } from "@/lib/api";

const STATUS_ICON: Record<VerificationStatus, typeof CheckCircle2> = {
  ok: CheckCircle2,
  warn: AlertTriangle,
  error: XCircle,
  empty: MinusCircle,
};

const STATUS_TONE: Record<VerificationStatus, string> = {
  ok: "text-emerald-600 dark:text-emerald-400",
  warn: "text-amber-600 dark:text-amber-400",
  error: "text-red-600 dark:text-red-400",
  empty: "text-muted-foreground",
};

interface Props {
  artifact: IndexPredictionArtifact | null | undefined;
  external?: PredictionUiExternalState;
}

export function PredictionVerificationPanel({ artifact, external }: Props) {
  const [open, setOpen] = useState(true);
  const audit = useMemo(
    () => runPredictionUiAudit(artifact, external),
    [artifact, external],
  );

  return (
    <section className="rounded-xl border bg-card shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            UI verification
          </p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            Each block: data source, user value, and whether it feeds the Nifty forecast.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <div className="flex gap-2 text-[10px] tabular-nums">
            <span className="text-emerald-600 dark:text-emerald-400">{audit.summary.ok} ok</span>
            <span className="text-amber-600 dark:text-amber-400">{audit.summary.warn} warn</span>
            <span className="text-red-600 dark:text-red-400">{audit.summary.error} err</span>
            <span className="text-muted-foreground">{audit.summary.empty} empty</span>
          </div>
          {open ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
        </div>
      </button>

      {open ? (
        <div className="border-t px-4 pb-4">
          <p className="py-2 text-[11px] text-muted-foreground">
            Macro model coverage:{" "}
            <span className="font-medium text-foreground">
              {audit.macroCoverage.present}/{audit.macroCoverage.total}
            </span>{" "}
            Ridge inputs populated
            {audit.macroCoverage.missing.length ? (
              <span className="ml-1 text-amber-700 dark:text-amber-400">
                (missing {audit.macroCoverage.missing.slice(0, 5).join(", ")}
                {audit.macroCoverage.missing.length > 5 ? "…" : ""})
              </span>
            ) : null}
          </p>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] border-collapse text-[11px]">
              <thead>
                <tr className="border-b text-left text-[10px] uppercase tracking-wide text-muted-foreground">
                  <th className="py-2 pr-3 font-semibold">Status</th>
                  <th className="py-2 pr-3 font-semibold">Component</th>
                  <th className="py-2 pr-3 font-semibold">Role</th>
                  <th className="py-2 pr-3 font-semibold">Source</th>
                  <th className="py-2 pr-3 font-semibold">Value / check</th>
                  <th className="py-2 font-semibold">In forecast?</th>
                </tr>
              </thead>
              <tbody>
                {audit.rows.map((row) => {
                  const Icon = STATUS_ICON[row.status];
                  return (
                    <tr key={row.id} className="border-b border-border/60 last:border-0">
                      <td className="py-2 pr-3">
                        <Icon className={cn("h-4 w-4", STATUS_TONE[row.status])} aria-hidden />
                      </td>
                      <td className="py-2 pr-3 font-medium">{row.component}</td>
                      <td className="py-2 pr-3">
                        <span
                          className={cn(
                            "rounded px-1.5 py-0.5 text-[10px] font-medium",
                            row.modelRole === "feeds" && "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
                            row.modelRole === "display" && "bg-primary/10 text-primary",
                            row.modelRole === "context" && "bg-muted text-muted-foreground",
                            row.modelRole === "verify" && "bg-violet-500/15 text-violet-700 dark:text-violet-400",
                            row.modelRole === "ops" && "bg-muted text-muted-foreground",
                          )}
                        >
                          {MODEL_ROLE_LABELS[row.modelRole]}
                        </span>
                      </td>
                      <td className="max-w-[200px] py-2 pr-3 text-muted-foreground">{row.source}</td>
                      <td className="py-2 pr-3">
                        <p>{row.userValue}</p>
                        {row.detail ? (
                          <p className="mt-0.5 text-[10px] text-muted-foreground">{row.detail}</p>
                        ) : null}
                      </td>
                      <td className="py-2">
                        {row.feedsForecast ? (
                          <span className="font-medium text-emerald-700 dark:text-emerald-400">Yes</span>
                        ) : (
                          <span className="text-muted-foreground">No</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </section>
  );
}
