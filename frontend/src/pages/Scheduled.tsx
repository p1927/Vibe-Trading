import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { CalendarClock, ChevronDown, ChevronRight, Loader2, Pause, Play, Plus, Square, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, ApiError, type ScheduledRun, type SchedulerRegistryEntry } from "@/lib/api";
import { LiveLogTail } from "@/components/scheduler/LiveLogTail";
import { ScheduledJobDetailPanel } from "@/components/scheduler/ScheduledJobDetailPanel";
import { StatusPill } from "@/components/scheduler/StatusPill";
import { JobsPanel } from "@/components/scheduler/JobsPanel";
import {
  describeCadence,
  formatIntervalMs,
  formatWallTime,
  formatWeekdays,
} from "@/lib/cadence";

const fieldClass =
  "w-full rounded-md border bg-background px-3 py-2 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-60";
const labelClass = "text-sm font-medium";
const hintClass = "text-xs text-muted-foreground";

const POLL_MS = 15_000;

// Mirrors the section keys computed server-side in
// scheduled_research/sections.py (job_section) — kept in the same order the
// tab strip renders them.
const SECTION_ORDER = [
  "prediction",
  "hub",
  "options",
  "trade_data",
  "autonomous_agent",
  "recording",
  "general",
] as const;

const SECTION_LABELS: Record<string, string> = {
  prediction: "Prediction",
  hub: "Hub / News",
  options: "Options",
  trade_data: "Trade Data",
  autonomous_agent: "Autonomous Agent",
  recording: "Recording",
  general: "General",
  // openalgo's five scheduler instances (Mechanism C) — see
  // openalgo/services/scheduler_registry_service.py's VALID_SOURCES.
  flow: "Flow",
  historify: "Historify",
  strategy: "Strategy",
  chartink: "Chartink",
  python_strategy: "Python Strategy",
};

// Recorder sections are dynamic (one per stock_simulator market:
// "recorder:us", "recorder:eu", ...), not a fixed set like Mechanism A's —
// derive a display label instead of hardcoding one per market.
function sectionLabel(section: string): string {
  if (SECTION_LABELS[section]) return SECTION_LABELS[section];
  if (section.startsWith("recorder:")) {
    return `Recorder (${section.slice("recorder:".length).toUpperCase()})`;
  }
  return section;
}

type DaysChoice = "every" | "weekdays";
type ComposerMode = "time" | "advanced";

function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function timezoneOptions(): string[] {
  // Not yet in the project's TS lib target, hence the local type.
  const supported = (Intl as { supportedValuesOf?: (key: "timeZone") => string[] })
    .supportedValuesOf;
  const zones = typeof supported === "function" ? supported("timeZone") : [browserTimezone()];
  return zones.includes("UTC") ? zones : ["UTC", ...zones];
}

// Jobs created before timezone support have timezone=null and keep their
// original UTC semantics; render them as UTC without converting anything.
function displayZone(run: ScheduledRun): string {
  return run.timezone ?? "UTC";
}

function formatInZone(epochMs: number, zone: string, locale: string): string {
  const date = new Date(epochMs);
  if (Number.isNaN(date.getTime())) return "—";
  try {
    return new Intl.DateTimeFormat(locale, {
      timeZone: zone,
      dateStyle: "medium",
      timeStyle: "short",
    }).format(date);
  } catch {
    return date.toISOString();
  }
}

export function Scheduled() {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;
  const [searchParams, setSearchParams] = useSearchParams();

  const [runs, setRuns] = useState<ScheduledRun[]>([]);
  // Read-only cross-service entries (Mechanism B/C — today just
  // stock_simulator's recorder categories). Kept separate from `runs`
  // rather than merged into one shape, since these have no pause/resume/
  // delete/cancel controls yet.
  const [registryEntries, setRegistryEntries] = useState<SchedulerRegistryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // At most one row's live-log-tail is mounted at a time — expanding a
  // second row collapses the first, so only one SSE connection is ever open.
  const [expandedLogKey, setExpandedLogKey] = useState<string | null>(null);
  // Kept separate from expandedLogKey — live-log-tail and job details are
  // independent panels, so both may be open on the same row at once.
  const [expandedDetailKey, setExpandedDetailKey] = useState<string | null>(null);
  // "all" plus whatever section the URL asked for (e.g. a deep link from the
  // Prediction tab's panel); an unknown/stale section value just falls back
  // to showing every run rather than an empty list.
  const [activeSection, setActiveSection] = useState<string>(
    () => searchParams.get("section") || "all",
  );
  // "Scheduler" (configuration, grouped by section) vs "Jobs" (runtime
  // monitoring — running first, live logs, pause/cancel/run-now). Orthogonal
  // to activeSection: its own `?view=` query param, same pattern.
  const [viewMode, setViewMode] = useState<"scheduler" | "jobs">(
    () => (searchParams.get("view") === "jobs" ? "jobs" : "scheduler"),
  );
  const [bulkResumeBusy, setBulkResumeBusy] = useState(false);

  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<ComposerMode>("time");
  const [time, setTime] = useState("09:00");
  const [days, setDays] = useState<DaysChoice>("weekdays");
  const [advanced, setAdvanced] = useState("");
  const [timezone, setTimezone] = useState(() => browserTimezone());
  // Delivery is opt-in: an empty channel is what every monitor had before, and
  // it means the briefing stays in the app.
  const [deliveryChannel, setDeliveryChannel] = useState("");
  const [deliveryTarget, setDeliveryTarget] = useState("");
  const [saving, setSaving] = useState(false);
  const [composerError, setComposerError] = useState<string | null>(null);

  const zonesRef = useRef<string[] | null>(null);
  if (zonesRef.current === null) zonesRef.current = timezoneOptions();

  // Stale-response guard: each refresh aborts the previous request and only
  // the newest sequence number may write state, so a slow 15s poll can never
  // overwrite the list a create/delete just updated.
  const refreshSeq = useRef(0);
  const refreshController = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    refreshController.current?.abort();
    const controller = new AbortController();
    refreshController.current = controller;
    const seq = ++refreshSeq.current;
    try {
      const [rows, registry] = await Promise.all([
        api.listScheduledRuns(controller.signal),
        // A down/unconfigured cross-service source must never break the
        // page's own job list — degrade to "no extra rows" instead.
        api.listSchedulerRegistry(controller.signal).catch(() => null),
      ]);
      if (seq !== refreshSeq.current) return;
      setRuns(rows);
      setRegistryEntries(registry?.entries ?? []);
      setListError(null);
    } catch (error) {
      if (seq !== refreshSeq.current || controller.signal.aborted) return;
      setListError(error instanceof ApiError ? error.message : String(error));
    } finally {
      if (seq === refreshSeq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => {
      refreshSeq.current++; // invalidate any in-flight response
      refreshController.current?.abort();
      clearInterval(timer);
    };
  }, [refresh]);

  // Auto-disarm an armed delete after a few seconds (blur is unreliable on
  // Safari/iOS, where buttons do not take focus on click).
  useEffect(() => {
    if (pendingDelete === null) return;
    const timer = setTimeout(() => setPendingDelete(null), 5_000);
    return () => clearTimeout(timer);
  }, [pendingDelete]);

  function composedSchedule(): string | null {
    if (mode === "advanced") {
      const spec = advanced.trim();
      return spec ? spec : null;
    }
    const match = /^(\d{1,2}):(\d{2})$/.exec(time);
    if (!match) return null;
    return `${Number(match[2])} ${Number(match[1])} * * ${days === "every" ? "*" : "1-5"}`;
  }

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    setComposerError(null);
    if (!prompt.trim()) {
      setComposerError(t("scheduled.promptRequired"));
      return;
    }
    const schedule = composedSchedule();
    if (schedule === null) {
      setComposerError(t("scheduled.invalidTime"));
      return;
    }
    setSaving(true);
    try {
      const channel = deliveryChannel.trim();
      const target = deliveryTarget.trim();
      if (channel && !target) {
        setComposerError(t("scheduled.deliveryTargetRequired"));
        return;
      }
      await api.createScheduledRun({
        prompt: prompt.trim(),
        schedule,
        timezone,
        delivery_channel: channel || null,
        delivery_target: channel ? target : null,
      });
      setPrompt("");
      await refresh();
    } catch (error) {
      setComposerError(error instanceof ApiError ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  async function handleConfirmedDelete(id: string) {
    setPendingDelete(null);
    try {
      await api.deleteScheduledRun(id);
      await refresh();
    } catch (error) {
      setListError(error instanceof ApiError ? error.message : String(error));
    }
  }

  async function handleTogglePause(run: ScheduledRun) {
    setBusyId(run.id);
    setActionError(null);
    try {
      if (run.paused) {
        await api.resumeScheduledRun(run.id);
      } else {
        await api.pauseScheduledRun(run.id);
      }
      await refresh();
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : String(error));
    } finally {
      setBusyId(null);
    }
  }

  async function handleToggleRegistryPause(entry: SchedulerRegistryEntry) {
    // openalgo entries are the only ones with controls.pause/resume today;
    // `entry.section` is that entry's scheduler source ("flow", "historify",
    // "strategy", "chartink", "python_strategy"), and the raw job id is
    // entry.id with the "C:<source>:" prefix stripped.
    const source = entry.section;
    const jobId = entry.id.slice(`C:${source}:`.length);
    setBusyId(entry.id);
    setActionError(null);
    try {
      if (entry.enabled) {
        await api.pauseSchedulerRegistryEntry(source, jobId);
      } else {
        await api.resumeSchedulerRegistryEntry(source, jobId);
      }
      await refresh();
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : String(error));
    } finally {
      setBusyId(null);
    }
  }

  async function handleCancelRun(run: ScheduledRun) {
    setBusyId(run.id);
    setActionError(null);
    try {
      await api.cancelScheduledRun(run.id);
      await refresh();
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : String(error));
    } finally {
      setBusyId(null);
    }
  }

  async function handleTriggerRun(run: ScheduledRun) {
    setBusyId(run.id);
    setActionError(null);
    try {
      await api.triggerScheduledRun(run.id);
      await refresh();
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : String(error));
    } finally {
      setBusyId(null);
    }
  }

  // The first *bulk* action in this file — deliberately uses Promise.allSettled
  // (not the single-call try/catch every other handler here uses) so one
  // failing job doesn't block the rest of a hot-reload's auto-paused jobs
  // from resuming.
  async function handleResumeAllAutoPaused() {
    const targets = runs.filter((r) => r.paused && r.auto_paused_reason);
    if (targets.length === 0) return;
    setBulkResumeBusy(true);
    setActionError(null);
    try {
      const results = await Promise.allSettled(targets.map((r) => api.resumeScheduledRun(r.id)));
      const failed = results.filter((r) => r.status === "rejected").length;
      if (failed > 0) {
        setActionError(
          t("scheduled.resumeAllPartialFailure", {
            resumed: targets.length - failed,
            total: targets.length,
            failed,
          }),
        );
      }
      await refresh();
    } finally {
      setBulkResumeBusy(false);
    }
  }

  function cadenceLabel(run: ScheduledRun): string {
    const cadence = describeCadence(run.schedule);
    switch (cadence.kind) {
      case "interval":
        return t("scheduled.cadenceEvery", { interval: formatIntervalMs(cadence.ms) });
      case "daily":
        return t("scheduled.cadenceDaily", { time: formatWallTime(cadence.hour, cadence.minute) });
      case "weekly":
        return t("scheduled.cadenceWeekly", {
          days: formatWeekdays(cadence.weekdays, locale),
          time: formatWallTime(cadence.hour, cadence.minute),
        });
      default:
        return cadence.expression;
    }
  }

  function verdictCell(run: ScheduledRun): ReactNode {
    const verdict = run.last_verdict;
    if (verdict === null) {
      return (
        <p className={hintClass} data-testid={`verdict-empty-${run.id}`}>
          {t("scheduled.verdictEmpty")}
        </p>
      );
    }
    // A monitor built from an ad-hoc prompt never produces a verdict section,
    // and that is its correct permanent state: render nothing, not a warning.
    if (verdict.parse === "no_verdict_section") {
      return null;
    }
    if (verdict.parse === "contract_violation") {
      // Never show a wrong verdict: the malformed run reads as unreadable, and
      // the prior good one is still visible through `previous` next release.
      return (
        <p className={hintClass} data-testid={`verdict-unreadable-${run.id}`}>
          {t("scheduled.verdictUnreadable")}
        </p>
      );
    }
    const when = formatInZone(verdict.recorded_at, displayZone(run), locale);
    if (verdict.items.length === 0) {
      return (
        <p className={hintClass} data-testid={`verdict-nocalls-${run.id}`}>
          {t("scheduled.verdictNoCalls")} · {t("scheduled.verdictRecorded", { when })}
        </p>
      );
    }
    const delta =
      verdict.previous && verdict.previous.outcome !== verdict.outcome
        ? t("scheduled.verdictDelta", { was: verdict.previous.outcome, now: verdict.outcome })
        : null;
    return (
      <p className="break-words text-xs text-muted-foreground" data-testid={`verdict-line-${run.id}`}>
        <span className="font-medium text-foreground">
          {verdict.items.map((item) => `${item.symbol} ${item.state}`).join(" · ")}
        </span>
        {" · "}
        {delta ? <span>{delta} · </span> : null}
        {t("scheduled.verdictRecorded", { when })}
      </p>
    );
  }

  function statusLabel(run: ScheduledRun): { label: string; tone: "success" | "danger" | "warning" | "neutral" } {
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

  function selectViewMode(mode: "scheduler" | "jobs") {
    setViewMode(mode);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (mode === "scheduler") {
          next.delete("view");
        } else {
          next.set("view", mode);
        }
        return next;
      },
      { replace: true },
    );
  }

  function selectSection(section: string) {
    setActiveSection(section);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (section === "all") {
          next.delete("section");
        } else {
          next.set("section", section);
        }
        return next;
      },
      { replace: true },
    );
  }

  const sectionCounts = new Map<string, number>();
  for (const run of runs) {
    sectionCounts.set(run.section, (sectionCounts.get(run.section) ?? 0) + 1);
  }
  for (const entry of registryEntries) {
    sectionCounts.set(entry.section, (sectionCounts.get(entry.section) ?? 0) + 1);
  }
  const recorderSections = [...sectionCounts.keys()]
    .filter((section) => section.startsWith("recorder:"))
    .sort();
  // openalgo's five scheduler sources ("flow", "historify", "strategy",
  // "chartink", "python_strategy") are dynamic too — any section outside
  // Mechanism A's fixed SECTION_ORDER and stock_simulator's "recorder:*"
  // prefix is one of these.
  const openalgoSections = [...sectionCounts.keys()]
    .filter(
      (section) =>
        !(SECTION_ORDER as readonly string[]).includes(section) &&
        !section.startsWith("recorder:"),
    )
    .sort();
  const visibleSections = [
    ...SECTION_ORDER.filter((section) => sectionCounts.has(section)),
    ...recorderSections,
    ...openalgoSections,
  ];
  const totalCount = runs.length + registryEntries.length;
  const filteredRuns =
    activeSection === "all" ? runs : runs.filter((run) => run.section === activeSection);
  const filteredRegistryEntries =
    activeSection === "all"
      ? registryEntries
      : registryEntries.filter((entry) => entry.section === activeSection);

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <header className="flex items-center gap-3">
        <CalendarClock className="h-6 w-6 text-primary" aria-hidden />
        <div>
          <h1 className="text-xl font-semibold">{t("scheduled.title")}</h1>
          <p className={hintClass}>{t("scheduled.subtitle")}</p>
        </div>
      </header>

      <div className="flex gap-1.5" role="tablist" aria-label={t("scheduled.viewModeLabel")}>
        <button
          type="button"
          role="tab"
          aria-selected={viewMode === "scheduler"}
          onClick={() => selectViewMode("scheduler")}
          className={cn(
            "rounded-full border px-3 py-1 text-xs transition",
            viewMode === "scheduler"
              ? "border-primary bg-primary/10 text-primary"
              : "text-muted-foreground hover:bg-muted",
          )}
        >
          {t("scheduled.viewScheduler")}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={viewMode === "jobs"}
          onClick={() => selectViewMode("jobs")}
          className={cn(
            "rounded-full border px-3 py-1 text-xs transition",
            viewMode === "jobs"
              ? "border-primary bg-primary/10 text-primary"
              : "text-muted-foreground hover:bg-muted",
          )}
        >
          {t("scheduled.viewJobs")}
        </button>
      </div>

      {viewMode === "jobs" && (
        <JobsPanel
          runs={runs}
          registryEntries={registryEntries}
          busyId={busyId}
          bulkResumeBusy={bulkResumeBusy}
          actionError={actionError}
          expandedLogKey={expandedLogKey}
          setExpandedLogKey={setExpandedLogKey}
          onTogglePause={handleTogglePause}
          onToggleRegistryPause={handleToggleRegistryPause}
          onCancelRun={handleCancelRun}
          onTriggerRun={handleTriggerRun}
          onResumeAllAutoPaused={handleResumeAllAutoPaused}
          streamUrlFor={(run) => () => api.scheduledRunStreamUrl(run.id)}
          formatNextRun={(run) =>
            t("scheduled.nextRun", { when: formatInZone(run.next_run_at, displayZone(run), locale) })
          }
        />
      )}

      {viewMode === "scheduler" && (
      <>
      <form onSubmit={handleCreate} className="space-y-4 rounded-lg border bg-card p-4">
        <div className="space-y-1.5">
          <label htmlFor="scheduled-prompt" className={labelClass}>
            {t("scheduled.promptLabel")}
          </label>
          <textarea
            id="scheduled-prompt"
            required
            rows={2}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={t("scheduled.promptPlaceholder")}
            className={fieldClass}
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="space-y-1.5">
            <label htmlFor="scheduled-mode" className={labelClass}>
              {t("scheduled.modeLabel")}
            </label>
            <select
              id="scheduled-mode"
              value={mode}
              onChange={(e) => setMode(e.target.value as ComposerMode)}
              className={fieldClass}
            >
              <option value="time">{t("scheduled.modeTime")}</option>
              <option value="advanced">{t("scheduled.modeAdvanced")}</option>
            </select>
          </div>

          {mode === "time" ? (
            <>
              <div className="space-y-1.5">
                <label htmlFor="scheduled-time" className={labelClass}>
                  {t("scheduled.timeLabel")}
                </label>
                <input
                  id="scheduled-time"
                  type="time"
                  required
                  value={time}
                  onChange={(e) => setTime(e.target.value)}
                  className={fieldClass}
                />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="scheduled-days" className={labelClass}>
                  {t("scheduled.daysLabel")}
                </label>
                <select
                  id="scheduled-days"
                  value={days}
                  onChange={(e) => setDays(e.target.value as DaysChoice)}
                  className={fieldClass}
                >
                  <option value="weekdays">{t("scheduled.daysWeekdays")}</option>
                  <option value="every">{t("scheduled.daysEveryDay")}</option>
                </select>
              </div>
            </>
          ) : (
            <div className="space-y-1.5 sm:col-span-2">
              <label htmlFor="scheduled-advanced" className={labelClass}>
                {t("scheduled.scheduleLabel")}
              </label>
              <input
                id="scheduled-advanced"
                required
                value={advanced}
                onChange={(e) => setAdvanced(e.target.value)}
                placeholder="30 23 * * 1-5"
                className={cn(fieldClass, "font-mono")}
              />
              <p className={hintClass}>{t("scheduled.advancedHint")}</p>
            </div>
          )}

          <div className="space-y-1.5">
            <label htmlFor="scheduled-timezone" className={labelClass}>
              {t("scheduled.timezoneLabel")}
            </label>
            <select
              id="scheduled-timezone"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              className={fieldClass}
            >
              {zonesRef.current.map((zone) => (
                <option key={zone} value={zone}>
                  {zone}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <label htmlFor="scheduled-delivery-channel" className={labelClass}>
              {t("scheduled.deliveryChannelLabel")}
            </label>
            <input
              id="scheduled-delivery-channel"
              value={deliveryChannel}
              onChange={(e) => setDeliveryChannel(e.target.value)}
              placeholder={t("scheduled.deliveryChannelPlaceholder")}
              className={fieldClass}
            />
            <p className={hintClass}>{t("scheduled.deliveryHint")}</p>
          </div>
          <div className="space-y-1.5">
            <label htmlFor="scheduled-delivery-target" className={labelClass}>
              {t("scheduled.deliveryTargetLabel")}
            </label>
            <input
              id="scheduled-delivery-target"
              value={deliveryTarget}
              onChange={(e) => setDeliveryTarget(e.target.value)}
              placeholder={t("scheduled.deliveryTargetPlaceholder")}
              className={fieldClass}
            />
          </div>
        </div>

        {composerError && (
          <p role="alert" className="text-sm text-danger">
            {composerError}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Plus className="h-4 w-4" aria-hidden />}
            {t("scheduled.create")}
          </button>
          <p className={hintClass}>{t("scheduled.executorHint")}</p>
        </div>
      </form>

      {!loading && totalCount > 0 && (
        <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Filter by section">
          <button
            type="button"
            role="tab"
            aria-selected={activeSection === "all"}
            onClick={() => selectSection("all")}
            className={cn(
              "rounded-full border px-3 py-1 text-xs transition",
              activeSection === "all"
                ? "border-primary bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-muted",
            )}
          >
            All ({totalCount})
          </button>
          {visibleSections.map((section) => (
            <button
              key={section}
              type="button"
              role="tab"
              aria-selected={activeSection === section}
              onClick={() => selectSection(section)}
              className={cn(
                "rounded-full border px-3 py-1 text-xs transition",
                activeSection === section
                  ? "border-primary bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-muted",
              )}
            >
              {sectionLabel(section)} ({sectionCounts.get(section)})
            </button>
          ))}
        </div>
      )}

      <section aria-label={t("scheduled.listTitle")} className="rounded-lg border bg-card">
        {loading ? (
          <div className="flex items-center justify-center gap-2 p-8 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            {t("scheduled.loading")}
          </div>
        ) : listError && runs.length === 0 ? (
          <div className="space-y-2 p-6 text-sm">
            <p className="text-danger">{listError}</p>
            <button
              type="button"
              onClick={() => void refresh()}
              className="rounded-md border px-3 py-1.5 text-sm transition hover:bg-muted"
            >
              {t("scheduled.retry")}
            </button>
          </div>
        ) : totalCount === 0 ? (
          <p className="p-8 text-center text-sm text-muted-foreground">{t("scheduled.empty")}</p>
        ) : filteredRuns.length === 0 && filteredRegistryEntries.length === 0 ? (
          <p className="p-8 text-center text-sm text-muted-foreground">
            No scheduled runs in {sectionLabel(activeSection)}.
          </p>
        ) : (
          <>
          {listError && (
            <p role="alert" className="border-b px-4 py-2 text-xs text-danger">
              {listError}
            </p>
          )}
          {actionError && (
            <p role="alert" className="border-b px-4 py-2 text-xs text-danger">
              {actionError}
            </p>
          )}
          <ul className="divide-y">
            {filteredRuns.map((run) => {
              const status = statusLabel(run);
              const zone = displayZone(run);
              return (
                <li key={run.id} className="flex flex-wrap items-start gap-3 p-4">
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{cadenceLabel(run)}</span>
                      <span className={hintClass}>{zone}</span>
                      <StatusPill label={status.label} tone={status.tone} />
                      {run.paused && run.auto_paused_reason ? (
                        <span title={run.auto_paused_reason}>
                          <StatusPill label={t("scheduled.autoPaused")} tone="warning" />
                        </span>
                      ) : run.paused ? (
                        <StatusPill label={t("scheduled.jobPaused")} tone="neutral" />
                      ) : null}
                    </div>
                    <p className="truncate text-sm text-muted-foreground">{run.prompt}</p>
                    <p className={hintClass}>
                      {t("scheduled.nextRun", {
                        when: formatInZone(run.next_run_at, zone, locale),
                      })}
                    </p>
                    {run.last_error && (
                      <p className="break-words text-xs text-danger">
                        {t("scheduled.lastError", { error: run.last_error })}
                      </p>
                    )}
                    {run.delivery_channel && (
                      <p
                        className={
                          run.delivery_status === "failed"
                            ? "break-words text-xs text-danger"
                            : hintClass
                        }
                      >
                        {t(`scheduled.delivery_${run.delivery_status}`, {
                          channel: run.delivery_channel,
                          defaultValue: t("scheduled.delivery_none", {
                            channel: run.delivery_channel,
                          }),
                        })}
                        {run.delivery_status === "failed" && run.delivery_error
                          ? ` — ${run.delivery_error}`
                          : ""}
                      </p>
                    )}
                    {verdictCell(run)}
                  </div>
                  {pendingDelete === run.id ? (
                    <div className="flex items-center gap-1.5">
                      {/* Cancel first so it inherits the Delete button's spot —
                          an accidental double-click disarms instead of destroying. */}
                      <button
                        type="button"
                        onClick={() => setPendingDelete(null)}
                        className="rounded-md border px-2.5 py-1.5 text-xs text-muted-foreground transition hover:bg-muted"
                      >
                        {t("layout.cancel")}
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleConfirmedDelete(run.id)}
                        aria-label={t("scheduled.confirmDeleteAria", { prompt: run.prompt })}
                        className="inline-flex items-center gap-1.5 rounded-md border border-danger bg-danger/10 px-2.5 py-1.5 text-xs text-danger transition"
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden />
                        {t("scheduled.confirmDelete")}
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1.5">
                      {run.status === "running" && (
                        <button
                          type="button"
                          disabled={busyId === run.id}
                          onClick={() => void handleCancelRun(run)}
                          aria-label={t("scheduled.cancelRunAria", { prompt: run.prompt })}
                          className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs text-muted-foreground transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          <Square className="h-3.5 w-3.5" aria-hidden />
                          {t("scheduled.cancelRun")}
                        </button>
                      )}
                      <button
                        type="button"
                        disabled={busyId === run.id}
                        onClick={() => void handleTogglePause(run)}
                        aria-label={
                          run.paused
                            ? t("scheduled.resumeJobAria", { prompt: run.prompt })
                            : t("scheduled.pauseJobAria", { prompt: run.prompt })
                        }
                        className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs text-muted-foreground transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {run.paused ? (
                          <Play className="h-3.5 w-3.5" aria-hidden />
                        ) : (
                          <Pause className="h-3.5 w-3.5" aria-hidden />
                        )}
                        {run.paused ? t("scheduled.resumeJob") : t("scheduled.pauseJob")}
                      </button>
                      <button
                        type="button"
                        onClick={() => setPendingDelete(run.id)}
                        aria-label={t("scheduled.deleteAria", { prompt: run.prompt })}
                        className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs text-muted-foreground transition hover:bg-muted"
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden />
                        {t("scheduled.delete")}
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          setExpandedLogKey((prev) => (prev === run.id ? null : run.id))
                        }
                        aria-expanded={expandedLogKey === run.id}
                        aria-label={t("scheduled.toggleLiveLog", { prompt: run.prompt })}
                        className="inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 text-xs text-muted-foreground transition hover:bg-muted"
                      >
                        {expandedLogKey === run.id ? (
                          <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                        ) : (
                          <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                        )}
                        {t("scheduled.liveLog")}
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          setExpandedDetailKey((prev) => (prev === run.id ? null : run.id))
                        }
                        aria-expanded={expandedDetailKey === run.id}
                        aria-label={t("scheduled.toggleDetailsAria", { prompt: run.prompt })}
                        className="inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 text-xs text-muted-foreground transition hover:bg-muted"
                      >
                        {expandedDetailKey === run.id ? (
                          <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                        ) : (
                          <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                        )}
                        {t("scheduled.viewDetails")}
                      </button>
                    </div>
                  )}
                  {expandedLogKey === run.id && (
                    <LiveLogTail streamUrl={() => api.scheduledRunStreamUrl(run.id)} />
                  )}
                  {expandedDetailKey === run.id && <ScheduledJobDetailPanel jobId={run.id} />}
                </li>
              );
            })}
          </ul>
          {filteredRegistryEntries.length > 0 && (
            <ul className="divide-y border-t">
              {filteredRegistryEntries.map((entry) => (
                <li key={entry.id} className="flex flex-wrap items-start gap-3 p-4">
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{entry.label}</span>
                      <StatusPill
                        label={entry.enabled ? "Active" : "Off"}
                        tone={entry.enabled ? "success" : "neutral"}
                      />
                      {!entry.controls.pause && !entry.controls.resume && (
                        <span className={hintClass}>read-only</span>
                      )}
                    </div>
                    <p className={hintClass}>{entry.schedule_display}</p>
                    {entry.next_run_at != null && (
                      <p className={hintClass}>
                        {t("scheduled.nextRun", {
                          when: formatInZone(entry.next_run_at, "UTC", locale),
                        })}
                      </p>
                    )}
                  </div>
                  {(entry.controls.pause || entry.controls.resume) && (
                    <button
                      type="button"
                      disabled={busyId === entry.id}
                      onClick={() => void handleToggleRegistryPause(entry)}
                      aria-label={
                        entry.enabled
                          ? t("scheduled.pauseJobAria", { prompt: entry.label })
                          : t("scheduled.resumeJobAria", { prompt: entry.label })
                      }
                      className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs text-muted-foreground transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {entry.enabled ? (
                        <Pause className="h-3.5 w-3.5" aria-hidden />
                      ) : (
                        <Play className="h-3.5 w-3.5" aria-hidden />
                      )}
                      {entry.enabled ? t("scheduled.pauseJob") : t("scheduled.resumeJob")}
                    </button>
                  )}
                  {entry.supports_live_log && entry.live_log_stream_url && (
                    <button
                      type="button"
                      onClick={() =>
                        setExpandedLogKey((prev) => (prev === entry.id ? null : entry.id))
                      }
                      aria-expanded={expandedLogKey === entry.id}
                      aria-label={t("scheduled.toggleLiveLog", { prompt: entry.label })}
                      className="inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 text-xs text-muted-foreground transition hover:bg-muted"
                    >
                      {expandedLogKey === entry.id ? (
                        <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                      ) : (
                        <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                      )}
                      {t("scheduled.liveLog")}
                    </button>
                  )}
                  {expandedLogKey === entry.id && entry.live_log_stream_url && (
                    <LiveLogTail streamUrl={() => Promise.resolve(entry.live_log_stream_url!)} />
                  )}
                </li>
              ))}
            </ul>
          )}
          </>
        )}
      </section>
      </>
      )}
    </div>
  );
}
