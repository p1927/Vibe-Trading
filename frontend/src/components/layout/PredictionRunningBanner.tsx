import { Link } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { usePredictionRunStore } from "@/stores/predictionRun";

interface Props {
  pathname: string;
}

export function PredictionRunningBanner({ pathname }: Props) {
  const running = usePredictionRunStore((s) => s.running);
  const runJobId = usePredictionRunStore((s) => s.runJobId);
  const ticker = usePredictionRunStore((s) => s.ticker);

  if (!running || pathname.startsWith("/prediction")) return null;

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-primary/20 bg-primary/10 px-4 py-2 text-xs text-foreground">
      <div className="flex items-center gap-2">
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
        <span>
          {ticker} analysis running
          {runJobId ? (
            <span className="ms-1 font-mono text-[10px] text-muted-foreground">
              ({runJobId.slice(0, 12)}…)
            </span>
          ) : null}
        </span>
      </div>
      <Link
        to="/prediction"
        className="shrink-0 rounded-md bg-primary px-2.5 py-1 text-[11px] font-semibold text-primary-foreground hover:bg-primary/90"
      >
        View progress
      </Link>
    </div>
  );
}
