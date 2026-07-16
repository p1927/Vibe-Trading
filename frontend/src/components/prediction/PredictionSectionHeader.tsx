import { cn } from "@/lib/utils";
import { MODEL_ROLE_LABELS, type ModelRole } from "@/lib/predictionVerification";

const ROLE_BADGE: Record<ModelRole, string> = {
  feeds: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
  display: "bg-primary/10 text-primary",
  context: "bg-muted text-muted-foreground",
  verify: "bg-violet-500/15 text-violet-700 dark:text-violet-400",
  ops: "bg-muted text-muted-foreground",
};

interface Props {
  title: string;
  subtitle?: string;
  modelRole?: ModelRole;
  className?: string;
}

/** Shared section heading for the Prediction page — badge stays top-right; title block wraps cleanly. */
export function PredictionSectionHeader({ title, subtitle, modelRole, className }: Props) {
  return (
    <div className={cn("mb-2 flex items-start justify-between gap-3", className)}>
      <div className="min-w-0 flex-1">
        <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{title}</p>
        {subtitle ? <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">{subtitle}</p> : null}
      </div>
      {modelRole ? (
        <span className={cn("shrink-0 rounded px-2 py-0.5 text-[10px] font-medium leading-none", ROLE_BADGE[modelRole])}>
          {MODEL_ROLE_LABELS[modelRole]}
        </span>
      ) : null}
    </div>
  );
}
