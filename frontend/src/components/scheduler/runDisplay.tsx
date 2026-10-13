import type { TFunction } from "i18next";
import type { ScheduledRun } from "@/lib/api";
import type { StatusTone } from "@/components/scheduler/StatusPill";

/** The one lifecycle-status -> pill mapping for a scheduled run (list view and Jobs view). */
export function runStatus(t: TFunction, run: ScheduledRun): { label: string; tone: StatusTone } {
  switch (run.status) {
    case "completed":
      return { label: t("scheduled.statusCompleted"), tone: "success" };
    case "failed":
      return { label: t("scheduled.statusFailed"), tone: "danger" };
    case "running":
      return { label: t("scheduled.statusRunning"), tone: "warning" };
    case "cancelled":
      return { label: t("scheduled.statusCancelled"), tone: "neutral" };
    case "expired":
      return { label: t("scheduled.statusExpired"), tone: "neutral" };
    default:
      return { label: t("scheduled.statusPending"), tone: "neutral" };
  }
}

// A handler summary's own `status` values that mean "the run finished, but not cleanly".
const DEGRADED = new Set(["degraded", "partial", "skipped"]);
const FAILED = new Set(["error", "failed"]);

/**
 * What the last run's `last_result_summary` says beyond "completed". A run can be recorded
 * completed while its handler reported `degraded`/`partial`/`skipped`; hiding that made a
 * half-working job read as healthy. Null when the summary is clean (or the job never ran).
 */
export function resultNote(run: ScheduledRun): { tone: StatusTone; text: string } | null {
  if (run.last_run_at == null) return null;
  const summary = run.last_result_summary;
  if (!summary) {
    return run.status === "completed"
      ? { tone: "neutral", text: "Completed, but no result summary was recorded." }
      : null;
  }
  const status = typeof summary.status === "string" ? summary.status.toLowerCase() : "";
  const failed = summary.had_errors === true || FAILED.has(status);
  if (!failed && !DEGRADED.has(status)) return null;
  const detail = [summary.reason, summary.error, summary.message].find(
    (v): v is string => typeof v === "string" && v.length > 0,
  );
  const label = failed ? "errors" : status;
  return { tone: failed ? "danger" : "warning", text: `Last result: ${label}${detail ? ` — ${detail}` : ""}` };
}

/** Status pills + the last-error / dispatch-blocked / result-summary lines shared by both run rows. */
export function RunNotes({ run, t }: { run: ScheduledRun; t: TFunction }) {
  const note = resultNote(run);
  return (
    <>
      {run.last_error && (
        <p className="break-words text-xs text-danger">{t("scheduled.lastError", { error: run.last_error })}</p>
      )}
      {run.dispatch_blocked_reason && (
        <p className="break-words text-xs text-warning" data-testid={`dispatch-blocked-${run.id}`}>
          {t("scheduled.dispatchBlocked", {
            reason: run.dispatch_blocked_reason,
            defaultValue: "Will not dispatch on this process: {{reason}}",
          })}
        </p>
      )}
      {note && (
        <p
          className={`break-words text-xs ${note.tone === "danger" ? "text-danger" : note.tone === "warning" ? "text-warning" : "text-muted-foreground"}`}
          data-testid={`result-note-${run.id}`}
        >
          {note.text}
        </p>
      )}
    </>
  );
}
