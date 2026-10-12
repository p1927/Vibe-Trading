import { AlertTriangle } from "lucide-react";
import { spotSuspectSummary } from "@/lib/forecastReplay/horizon";
import type { ReplayRecord } from "@/lib/forecastReplay/types";

/** Warning when the recorded spot of a forecast differs from the issue-day close (`meta.spot_suspect`). */
export function SpotSuspectBadge({ records }: { records: ReplayRecord[] }) {
  const s = spotSuspectSummary(records);
  if (!s) return null;
  const gap = s.gapPct == null ? "" : ` by ${Math.abs(s.gapPct).toFixed(1)}%`;
  const tip = `${s.count} record${s.count === 1 ? "" : "s"}: the spot saved with the forecast differs from that day's close${gap}. ` +
    "The recorded path and errors are measured from the saved spot, so treat them with caution.";
  return (
    <span
      title={tip}
      data-testid="spot-suspect-badge"
      className="ml-1.5 inline-flex items-center gap-0.5 rounded border border-amber-500/50 bg-amber-500/10 px-1 text-[9px] font-medium text-amber-700 dark:text-amber-400"
    >
      <AlertTriangle className="h-2.5 w-2.5" />
      spot differs from close{gap}
    </span>
  );
}
