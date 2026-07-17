import { Newspaper, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  materialNewsCount?: number;
  lastReason?: string | null;
  countdownSec?: number;
  pollMs?: number;
  monitorEnabled?: boolean;
  pausedForAnalysis?: boolean;
}

export function NewsTriggerPanel({
  materialNewsCount = 0,
  lastReason,
  countdownSec = 0,
  pollMs = 0,
  monitorEnabled,
  pausedForAnalysis = false,
}: Props) {
  if (!monitorEnabled) {
    return (
      <div className="rounded-xl border border-dashed bg-muted/20 px-4 py-3 text-[12px] text-muted-foreground">
        Live refresh off — enable an interval above to update prediction as news and macro factors shift.
      </div>
    );
  }

  const mins = Math.floor(pollMs / 60_000);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card px-4 py-3 shadow-sm">
      <div className="flex items-center gap-2 text-[12px]">
        <Newspaper className="h-4 w-4 text-muted-foreground" />
        <span>
          Material headlines since last run:{" "}
          <span className={cn("font-semibold", materialNewsCount > 0 && "text-amber-600 dark:text-amber-400")}>
            {materialNewsCount}
          </span>
        </span>
      </div>
      <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
        <RefreshCw className="h-3.5 w-3.5" />
        <span>
          {pausedForAnalysis
            ? "Live refresh paused during analysis"
            : `Next refresh in ${countdownSec}s`}
          {lastReason ? ` · last: ${lastReason.replace(/_/g, " ")}` : ""}
          {mins > 0 ? ` · every ${mins}m` : ""}
        </span>
      </div>
    </div>
  );
}
