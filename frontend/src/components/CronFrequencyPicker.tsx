import { useState } from "react";
import { cn } from "@/lib/utils";

interface CronPreset {
  label: string;
  cron: string;
}

const CRON_PRESETS: CronPreset[] = [
  { label: "5 min", cron: "*/5 * * * *" },
  { label: "15 min", cron: "*/15 * * * *" },
  { label: "30 min", cron: "*/30 * * * *" },
  { label: "Hourly", cron: "0 * * * *" },
  { label: "3h", cron: "0 */3 * * *" },
  { label: "6h", cron: "0 */6 * * *" },
  { label: "12h", cron: "0 */12 * * *" },
  { label: "Daily", cron: "0 0 * * *" },
];

function matchingPreset(value: string): CronPreset | undefined {
  const trimmed = (value || "").trim();
  return CRON_PRESETS.find((preset) => preset.cron === trimmed);
}

export function CronFrequencyPicker({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (cron: string) => void;
  disabled?: boolean;
}) {
  const preset = matchingPreset(value);
  const [forceCustom, setForceCustom] = useState(false);
  const isCustom = forceCustom || !preset;

  return (
    <div className="space-y-1.5">
      <div className={cn("inline-flex flex-wrap gap-0.5 rounded-lg border p-0.5 text-[11px]", disabled && "opacity-50")}>
        {CRON_PRESETS.map((option) => (
          <button
            key={option.cron}
            type="button"
            disabled={disabled}
            onClick={() => {
              setForceCustom(false);
              onChange(option.cron);
            }}
            className={cn(
              "rounded-md px-2 py-1",
              !isCustom && preset?.cron === option.cron
                ? "bg-muted font-medium"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {option.label}
          </button>
        ))}
        <button
          type="button"
          disabled={disabled}
          onClick={() => setForceCustom(true)}
          className={cn(
            "rounded-md px-2 py-1",
            isCustom ? "bg-muted font-medium" : "text-muted-foreground hover:text-foreground",
          )}
        >
          Custom
        </button>
      </div>
      {isCustom ? (
        <input
          type="text"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          placeholder="standard 5-field cron, e.g. */15 * * * *"
          className="w-full rounded-md border bg-background px-2 py-1.5 font-mono text-[12px] disabled:opacity-50"
        />
      ) : null}
    </div>
  );
}
