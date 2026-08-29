import { cn } from "@/lib/utils";

export type StatusTone = "success" | "danger" | "warning" | "neutral";

export function StatusPill({ label, tone }: { label: string; tone: StatusTone }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-2 py-0.5 text-xs font-medium",
        tone === "success" && "bg-success/10 text-success",
        tone === "danger" && "bg-danger/10 text-danger",
        tone === "warning" && "bg-warning/10 text-warning",
        tone === "neutral" && "bg-muted text-muted-foreground",
      )}
    >
      {label}
    </span>
  );
}
