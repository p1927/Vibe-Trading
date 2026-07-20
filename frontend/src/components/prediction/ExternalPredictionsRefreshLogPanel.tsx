import { useEffect, useRef } from "react";
import { Terminal } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ExternalRefreshPhase } from "@/hooks/useExternalPredictions";
import type { PipelineLogEntry } from "@/lib/api";
import { formatPipelineLogTime, pipelineLogRowKey } from "@/lib/pipelineLogUtils";

const LEVEL_STYLES: Record<string, string> = {
  info: "text-foreground/90",
  warn: "text-amber-700 dark:text-amber-400",
  error: "text-red-700 dark:text-red-400",
};

const PHASE_LABELS: Record<ExternalRefreshPhase, string | null> = {
  idle: null,
  starting: "Connecting to API…",
  running: "Running…",
  reattaching: "Re-attaching to run…",
};

interface Props {
  logs: PipelineLogEntry[];
  refreshing: boolean;
  refreshPhase?: ExternalRefreshPhase;
  className?: string;
}

export function ExternalPredictionsRefreshLogPanel({
  logs,
  refreshing,
  refreshPhase = "idle",
  className,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const phaseLabel = refreshing ? PHASE_LABELS[refreshPhase] : null;

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [logs.length, refreshing]);

  if (!refreshing && !logs.length) return null;

  return (
    <section
      className={cn(
        "overflow-hidden rounded-xl border border-border/60 bg-card/40 shadow-sm",
        className,
      )}
    >
      <div className="flex items-center gap-2 border-b border-border/60 px-3 py-2">
        <Terminal className="h-3.5 w-3.5 text-muted-foreground" />
        <h2 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          Refresh activity
        </h2>
        {phaseLabel ? (
          <span className="ms-auto text-[10px] font-medium text-primary">{phaseLabel}</span>
        ) : null}
      </div>
      <div
        ref={scrollRef}
        className="max-h-64 overflow-y-auto bg-muted/20 px-3 py-2 font-mono text-[10px] leading-relaxed"
      >
        {!logs.length && refreshing ? (
          <p className="text-muted-foreground">
            {refreshPhase === "starting"
              ? "Waiting for API to accept the refresh job…"
              : "Waiting for backend logs…"}
          </p>
        ) : null}
        {logs.map((entry, idx) => (
          <div key={pipelineLogRowKey(entry, idx)} className="flex gap-2 py-0.5">
            <span className="shrink-0 text-muted-foreground">{formatPipelineLogTime(entry.at)}</span>
            <span className="shrink-0 uppercase text-primary/80">{entry.stage}</span>
            <span className={cn("min-w-0 break-words", LEVEL_STYLES[entry.level ?? "info"] ?? LEVEL_STYLES.info)}>
              {entry.message}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
