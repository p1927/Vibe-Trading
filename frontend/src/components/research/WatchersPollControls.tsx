import { WATCHERS_POLL_OPTIONS } from "@/lib/pollIntervalOptions";
import { cn } from "@/lib/utils";

interface Props {
  pollMs: number;
  onPollChange: (ms: number) => void;
  countdownSec: number;
  liveEnabled: boolean;
  fetchedAt?: string;
  className?: string;
}

export function WatchersPollControls({
  pollMs,
  onPollChange,
  countdownSec,
  liveEnabled,
  fetchedAt,
  className,
}: Props) {
  return (
    <div className={cn("flex flex-wrap items-center justify-between gap-2", className)}>
      <label className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
        <span>Live refresh</span>
        <select
          value={pollMs}
          onChange={(e) => onPollChange(Number(e.target.value))}
          className="rounded border bg-background px-1.5 py-0.5 text-[10px] text-foreground"
        >
          {WATCHERS_POLL_OPTIONS.map((o) => (
            <option key={o.ms} value={o.ms}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <div className="text-[10px] text-muted-foreground">
        {liveEnabled ? (
          <span>Next refresh in {countdownSec}s</span>
        ) : (
          <span>Live updates off</span>
        )}
        {fetchedAt && (
          <span className="ml-2 tabular-nums">
            · {new Date(fetchedAt).toLocaleTimeString()}
          </span>
        )}
      </div>
    </div>
  );
}
