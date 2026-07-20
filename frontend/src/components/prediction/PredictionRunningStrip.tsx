import { Loader2 } from "lucide-react";
import type { PipelineLogEntry } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  running: boolean;
  runJobId?: string | null;
  pipelineLogs: PipelineLogEntry[];
  predictionMode: "analysis" | "news-scenarios" | "scoreboard" | "external-predictions";
  onGoToAnalysis: () => void;
}

export function PredictionRunningStrip({
  running,
  runJobId,
  pipelineLogs,
  predictionMode,
  onGoToAnalysis,
}: Props) {
  if (!running) return null;

  const latestLog = pipelineLogs.length ? pipelineLogs[pipelineLogs.length - 1] : null;

  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-between gap-2 rounded-lg border border-primary/25 bg-primary/5 px-3 py-2 text-[12px]",
      )}
      role="status"
      aria-live="polite"
    >
      <div className="flex min-w-0 flex-1 items-start gap-2">
        <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-primary" />
        <div className="min-w-0">
          <p className="font-medium text-foreground">
            Analysis running…
            {runJobId ? (
              <span className="ms-1.5 font-mono text-[10px] font-normal text-muted-foreground">
                {runJobId.slice(0, 12)}…
              </span>
            ) : null}
          </p>
          {latestLog ? (
            <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
              <span className="uppercase">{latestLog.stage}</span>: {latestLog.message}
            </p>
          ) : (
            <p className="mt-0.5 text-[11px] text-muted-foreground">Starting pipeline…</p>
          )}
        </div>
      </div>
      {predictionMode !== "analysis" ? (
        <button
          type="button"
          onClick={onGoToAnalysis}
          className="shrink-0 rounded-md border border-primary/30 bg-background px-2.5 py-1 text-[11px] font-semibold text-primary hover:bg-primary/10"
        >
          Go to Analysis
        </button>
      ) : null}
    </div>
  );
}
