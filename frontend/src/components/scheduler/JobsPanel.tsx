import type { TFunction } from "i18next";
import { ChevronDown, ChevronRight, Loader2, Pause, Play, PlayCircle, Square } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import type { ScheduledRun, SchedulerRegistryEntry } from "@/lib/api";
import { LiveLogTail } from "@/components/scheduler/LiveLogTail";
import { StatusPill, type StatusTone } from "@/components/scheduler/StatusPill";

const hintClass = "text-xs text-muted-foreground";

interface JobsPanelProps {
  runs: ScheduledRun[];
  registryEntries: SchedulerRegistryEntry[];
  busyId: string | null;
  bulkResumeBusy: boolean;
  actionError: string | null;
  expandedLogKey: string | null;
  setExpandedLogKey: (updater: (prev: string | null) => string | null) => void;
  onTogglePause: (run: ScheduledRun) => void;
  onToggleRegistryPause: (entry: SchedulerRegistryEntry) => void;
  onCancelRun: (run: ScheduledRun) => void;
  onTriggerRun: (run: ScheduledRun) => void;
  onResumeAllAutoPaused: () => void;
  streamUrlFor: (run: ScheduledRun) => () => Promise<string>;
  formatNextRun: (run: ScheduledRun) => string;
}

function jobStatusLabel(t: TFunction, run: ScheduledRun): { label: string; tone: StatusTone } {
  switch (run.status) {
    case "completed":
      return { label: t("scheduled.statusCompleted"), tone: "success" };
    case "failed":
      return { label: t("scheduled.statusFailed"), tone: "danger" };
    case "running":
      return { label: t("scheduled.statusRunning"), tone: "warning" };
    case "cancelled":
      return { label: t("scheduled.statusCancelled"), tone: "neutral" };
    default:
      return { label: t("scheduled.statusPending"), tone: "neutral" };
  }
}

type JobRow =
  | { kind: "run"; key: string; run: ScheduledRun }
  | { kind: "registry"; key: string; entry: SchedulerRegistryEntry };

// Four buckets, sorted "running first":
//  0 = currently running (Mechanism A only — the only source with real run-state)
//  1 = paused / auto-paused (Mechanism A)
//  2 = always-on background (Mechanism B recorders — no on/off run-state, not "running now")
//  3 = idle/scheduled (everything else, including all Mechanism C rows)
function sortKey(row: JobRow): number {
  if (row.kind === "run") {
    if (row.run.status === "running") return 0;
    if (row.run.paused) return 1;
    return 3;
  }
  return row.entry.source === "stock_simulator" ? 2 : 3;
}

const actionButtonClass =
  "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs text-muted-foreground transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60";

export function JobsPanel({
  runs,
  registryEntries,
  busyId,
  bulkResumeBusy,
  actionError,
  expandedLogKey,
  setExpandedLogKey,
  onTogglePause,
  onToggleRegistryPause,
  onCancelRun,
  onTriggerRun,
  onResumeAllAutoPaused,
  streamUrlFor,
  formatNextRun,
}: JobsPanelProps) {
  const { t } = useTranslation();

  const autoPaused = runs.filter((run) => run.paused && run.auto_paused_reason);
  const runningNow = runs.filter((run) => run.status === "running").length;
  const pausedCount = runs.filter((run) => run.paused).length;
  const alwaysOnCount = registryEntries.filter((entry) => entry.source === "stock_simulator").length;
  const openalgoCount = registryEntries.filter((entry) => entry.source === "openalgo").length;

  const merged: JobRow[] = [
    ...runs.map((run) => ({ kind: "run" as const, key: run.id, run })),
    ...registryEntries.map((entry) => ({ kind: "registry" as const, key: entry.id, entry })),
  ].sort((a, b) => sortKey(a) - sortKey(b));

  return (
    <div className="space-y-4">
      {autoPaused.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-warning/40 bg-warning/10 px-4 py-3">
          <p className="text-sm text-warning">
            {t("scheduled.autoPausedBanner", { count: autoPaused.length })}
          </p>
          <button
            type="button"
            disabled={bulkResumeBusy}
            onClick={() => void onResumeAllAutoPaused()}
            aria-label={t("scheduled.resumeAllAutoPausedAria")}
            className="inline-flex items-center gap-1.5 rounded-md border border-warning bg-warning/10 px-2.5 py-1.5 text-xs text-warning transition disabled:cursor-not-allowed disabled:opacity-60"
          >
            {bulkResumeBusy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <Play className="h-3.5 w-3.5" aria-hidden />
            )}
            {t("scheduled.resumeAllAutoPaused")}
          </button>
        </div>
      )}

      <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
        <span>
          {t("scheduled.jobsRunningNow")}: <strong className="text-foreground">{runningNow}</strong>
        </span>
        <span>
          {t("scheduled.jobsPaused")}: <strong className="text-foreground">{pausedCount}</strong>
        </span>
        <span>
          {t("scheduled.jobsAutoPaused")}: <strong className="text-foreground">{autoPaused.length}</strong>
        </span>
        <span>
          {t("scheduled.jobsAlwaysOn")}: <strong className="text-foreground">{alwaysOnCount}</strong>
        </span>
        <span>
          {t("scheduled.jobsOpenalgoCount")}: <strong className="text-foreground">{openalgoCount}</strong>
        </span>
      </div>

      {actionError && (
        <p role="alert" className="text-xs text-danger">
          {actionError}
        </p>
      )}

      <div className="rounded-lg border bg-card">
        {merged.length === 0 ? (
          <p className="p-8 text-center text-sm text-muted-foreground">{t("scheduled.jobsEmpty")}</p>
        ) : (
          <ul className="divide-y">
            {merged.map((row) =>
              row.kind === "run" ? (
                <RunRow
                  key={row.key}
                  run={row.run}
                  busyId={busyId}
                  expandedLogKey={expandedLogKey}
                  setExpandedLogKey={setExpandedLogKey}
                  onTogglePause={onTogglePause}
                  onCancelRun={onCancelRun}
                  onTriggerRun={onTriggerRun}
                  streamUrlFor={streamUrlFor}
                  formatNextRun={formatNextRun}
                />
              ) : (
                <RegistryRow
                  key={row.key}
                  entry={row.entry}
                  busyId={busyId}
                  expandedLogKey={expandedLogKey}
                  setExpandedLogKey={setExpandedLogKey}
                  onToggleRegistryPause={onToggleRegistryPause}
                />
              ),
            )}
          </ul>
        )}
      </div>
    </div>
  );
}

function RunRow({
  run,
  busyId,
  expandedLogKey,
  setExpandedLogKey,
  onTogglePause,
  onCancelRun,
  onTriggerRun,
  streamUrlFor,
  formatNextRun,
}: {
  run: ScheduledRun;
  busyId: string | null;
  expandedLogKey: string | null;
  setExpandedLogKey: (updater: (prev: string | null) => string | null) => void;
  onTogglePause: (run: ScheduledRun) => void;
  onCancelRun: (run: ScheduledRun) => void;
  onTriggerRun: (run: ScheduledRun) => void;
  streamUrlFor: (run: ScheduledRun) => () => Promise<string>;
  formatNextRun: (run: ScheduledRun) => string;
}) {
  const { t } = useTranslation();
  const status = jobStatusLabel(t, run);
  const canTrigger = !run.paused && run.status !== "running";

  return (
    <li className="flex flex-wrap items-start gap-3 p-4">
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{run.prompt}</span>
          <StatusPill label={status.label} tone={status.tone} />
          {run.paused && run.auto_paused_reason ? (
            <span title={run.auto_paused_reason}>
              <StatusPill label={t("scheduled.autoPaused")} tone="warning" />
            </span>
          ) : run.paused ? (
            <StatusPill label={t("scheduled.jobPaused")} tone="neutral" />
          ) : null}
        </div>
        <p className={hintClass}>{formatNextRun(run)}</p>
        {run.last_error && (
          <p className="break-words text-xs text-danger">
            {t("scheduled.lastError", { error: run.last_error })}
          </p>
        )}
      </div>
      <div className="flex items-center gap-1.5">
        {run.status === "running" && (
          <button
            type="button"
            disabled={busyId === run.id}
            onClick={() => void onCancelRun(run)}
            aria-label={t("scheduled.cancelRunAria", { prompt: run.prompt })}
            className={actionButtonClass}
          >
            <Square className="h-3.5 w-3.5" aria-hidden />
            {t("scheduled.cancelRun")}
          </button>
        )}
        {canTrigger && (
          <button
            type="button"
            disabled={busyId === run.id}
            onClick={() => void onTriggerRun(run)}
            aria-label={t("scheduled.triggerRunAria", { prompt: run.prompt })}
            className={actionButtonClass}
          >
            <PlayCircle className="h-3.5 w-3.5" aria-hidden />
            {t("scheduled.triggerRun")}
          </button>
        )}
        <button
          type="button"
          disabled={busyId === run.id}
          onClick={() => void onTogglePause(run)}
          aria-label={
            run.paused
              ? t("scheduled.resumeJobAria", { prompt: run.prompt })
              : t("scheduled.pauseJobAria", { prompt: run.prompt })
          }
          className={actionButtonClass}
        >
          {run.paused ? <Play className="h-3.5 w-3.5" aria-hidden /> : <Pause className="h-3.5 w-3.5" aria-hidden />}
          {run.paused ? t("scheduled.resumeJob") : t("scheduled.pauseJob")}
        </button>
        <button
          type="button"
          onClick={() => setExpandedLogKey((prev) => (prev === run.id ? null : run.id))}
          aria-expanded={expandedLogKey === run.id}
          aria-label={t("scheduled.toggleLiveLog", { prompt: run.prompt })}
          className={cn(actionButtonClass, "gap-1")}
        >
          {expandedLogKey === run.id ? (
            <ChevronDown className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" aria-hidden />
          )}
          {t("scheduled.liveLog")}
        </button>
      </div>
      {expandedLogKey === run.id && <LiveLogTail streamUrl={streamUrlFor(run)} />}
    </li>
  );
}

function RegistryRow({
  entry,
  busyId,
  expandedLogKey,
  setExpandedLogKey,
  onToggleRegistryPause,
}: {
  entry: SchedulerRegistryEntry;
  busyId: string | null;
  expandedLogKey: string | null;
  setExpandedLogKey: (updater: (prev: string | null) => string | null) => void;
  onToggleRegistryPause: (entry: SchedulerRegistryEntry) => void;
}) {
  const { t } = useTranslation();
  const isAlwaysOn = entry.source === "stock_simulator";

  return (
    <li className="flex flex-wrap items-start gap-3 p-4">
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{entry.label}</span>
          {isAlwaysOn ? (
            <StatusPill label={t("scheduled.jobsAlwaysOn")} tone="neutral" />
          ) : (
            <StatusPill
              label={entry.enabled ? "Active" : "Off"}
              tone={entry.enabled ? "success" : "neutral"}
            />
          )}
          {!entry.controls.pause && !entry.controls.resume && (
            <span className={hintClass}>read-only</span>
          )}
        </div>
        <p className={hintClass}>{entry.schedule_display}</p>
      </div>
      <div className="flex items-center gap-1.5">
        {/* No backend support for registry trigger-now yet — controls.trigger_now
            is hardcoded false for every source today (see
            openalgo/services/scheduler_registry_service.py and
            stock_simulator/service/scheduler_introspection.py), so this button
            is intentionally absent rather than rendered disabled. */}
        {(entry.controls.pause || entry.controls.resume) && (
          <button
            type="button"
            disabled={busyId === entry.id}
            onClick={() => void onToggleRegistryPause(entry)}
            aria-label={
              entry.enabled
                ? t("scheduled.pauseJobAria", { prompt: entry.label })
                : t("scheduled.resumeJobAria", { prompt: entry.label })
            }
            className={actionButtonClass}
          >
            {entry.enabled ? <Pause className="h-3.5 w-3.5" aria-hidden /> : <Play className="h-3.5 w-3.5" aria-hidden />}
            {entry.enabled ? t("scheduled.pauseJob") : t("scheduled.resumeJob")}
          </button>
        )}
        {entry.supports_live_log && entry.live_log_stream_url && (
          <button
            type="button"
            onClick={() => setExpandedLogKey((prev) => (prev === entry.id ? null : entry.id))}
            aria-expanded={expandedLogKey === entry.id}
            aria-label={t("scheduled.toggleLiveLog", { prompt: entry.label })}
            className={cn(actionButtonClass, "gap-1")}
          >
            {expandedLogKey === entry.id ? (
              <ChevronDown className="h-3.5 w-3.5" aria-hidden />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            )}
            {t("scheduled.liveLog")}
          </button>
        )}
      </div>
      {expandedLogKey === entry.id && entry.live_log_stream_url && (
        <LiveLogTail streamUrl={() => Promise.resolve(entry.live_log_stream_url!)} />
      )}
    </li>
  );
}
