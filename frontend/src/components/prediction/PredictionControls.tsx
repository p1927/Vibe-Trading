import { Loader2, PanelRight, Play } from "lucide-react";
import { cn } from "@/lib/utils";

export const HORIZON_OPTIONS = [
  { label: "Tactical (2d)", days: 2, name: "A" },
  { label: "Short (7d)", days: 7, name: "A" },
  { label: "Default (14d)", days: 14, name: "B" },
  { label: "Swing (30d)", days: 30, name: "B" },
  { label: "Structural (60d)", days: 60, name: "C" },
] as const;

export const POLL_OPTIONS = [
  { label: "Off", ms: 0 },
  { label: "1 min", ms: 60_000 },
  { label: "5 min", ms: 300_000 },
  { label: "15 min", ms: 900_000 },
  { label: "30 min", ms: 1_800_000 },
] as const;

interface Props {
  horizonDays: number;
  onHorizonChange: (days: number) => void;
  pollMs: number;
  onPollChange: (ms: number) => void;
  refreshConstituents: boolean;
  onRefreshConstituentsChange: (value: boolean) => void;
  onRun: () => void;
  running?: boolean;
  lastUpdated?: string | null;
  spot?: number | null;
  regime?: string | null;
  pipelinePanelOpen?: boolean;
  onTogglePipelinePanel?: () => void;
}

export function PredictionControls({
  horizonDays,
  onHorizonChange,
  pollMs,
  onPollChange,
  refreshConstituents,
  onRefreshConstituentsChange,
  onRun,
  running,
  lastUpdated,
  spot,
  regime,
  pipelinePanelOpen,
  onTogglePipelinePanel,
}: Props) {
  const regimeTone = (() => {
    const r = (regime || "").toLowerCase();
    if (r.includes("bull") || r.includes("risk_on")) return "text-emerald-600 dark:text-emerald-400";
    if (r.includes("bear") || r.includes("risk_off")) return "text-red-600 dark:text-red-400";
    return "text-muted-foreground";
  })();

  return (
    <div className="flex flex-col gap-4 rounded-xl border bg-card p-4 shadow-sm sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-2">
          <h1 className="text-lg font-semibold tracking-tight">NIFTY 50 Prediction</h1>
          {spot != null && Number.isFinite(spot) ? (
            <span className="text-sm font-medium tabular-nums">{spot.toLocaleString("en-IN", { maximumFractionDigits: 2 })}</span>
          ) : null}
          {regime ? (
            <span className={cn("text-[11px] font-medium uppercase tracking-wide", regimeTone)}>
              {regime.replace(/_/g, " ")}
            </span>
          ) : null}
        </div>
        {lastUpdated ? (
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            Updated {new Date(lastUpdated).toLocaleString()}
          </p>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <select
          className="h-9 rounded-lg border bg-background px-2 text-sm"
          value={horizonDays}
          onChange={(e) => onHorizonChange(Number(e.target.value))}
          aria-label="Prediction horizon"
        >
          {HORIZON_OPTIONS.map((o) => (
            <option key={o.days} value={o.days}>
              {o.label}
            </option>
          ))}
        </select>

        <select
          className="h-9 rounded-lg border bg-background px-2 text-sm"
          value={pollMs}
          onChange={(e) => onPollChange(Number(e.target.value))}
          aria-label="Live refresh interval"
        >
          {POLL_OPTIONS.map((o) => (
            <option key={o.ms} value={o.ms}>
              Live: {o.label}
            </option>
          ))}
        </select>

        <label className="inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-lg border bg-background px-2 text-[11px]">
          <input
            type="checkbox"
            checked={refreshConstituents}
            onChange={(e) => onRefreshConstituentsChange(e.target.checked)}
            className="rounded border-border"
          />
          Refresh all 50 constituents
        </label>

        <button
          type="button"
          onClick={onTogglePipelinePanel}
          className={cn(
            "inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm",
            pipelinePanelOpen ? "bg-muted" : "bg-background",
          )}
          aria-pressed={pipelinePanelOpen}
          aria-label="Toggle pipeline activity panel"
        >
          <PanelRight className="h-4 w-4" />
          Activity
        </button>

        <button
          type="button"
          onClick={onRun}
          disabled={running}
          className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
        >
          {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
          Run analysis
        </button>
      </div>
    </div>
  );
}
