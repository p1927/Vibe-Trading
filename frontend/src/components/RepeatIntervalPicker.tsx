import { useMemo, useState } from "react";
import { cn } from "@/lib/utils";

type RepeatKind = "minutes" | "hourly" | "daily" | "weekly" | "monthly";

const MINUTE_OPTIONS = [5, 10, 15, 30, 45];
const DOW_LABELS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

interface ParsedCron {
  kind: RepeatKind;
  minuteInterval: number;
  minute: number;
  hour: number;
  dayOfWeek: number;
  dayOfMonth: number;
}

const DEFAULT_PARSED: ParsedCron = {
  kind: "daily",
  minuteInterval: 15,
  minute: 0,
  hour: 0,
  dayOfWeek: 1,
  dayOfMonth: 1,
};

function parseCron(value: string): ParsedCron | null {
  const fields = (value || "").trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const [min, hour, dom, month, dow] = fields;

  const everyNMinutes = /^\*\/(\d+)$/.exec(min);
  if (everyNMinutes && hour === "*" && dom === "*" && month === "*" && dow === "*") {
    return { ...DEFAULT_PARSED, kind: "minutes", minuteInterval: parseInt(everyNMinutes[1], 10) };
  }

  if (min === "0" && hour === "*" && dom === "*" && month === "*" && dow === "*") {
    return { ...DEFAULT_PARSED, kind: "hourly" };
  }

  const minuteNum = /^\d+$/.test(min) ? parseInt(min, 10) : null;
  const hourNum = /^\d+$/.test(hour) ? parseInt(hour, 10) : null;
  if (minuteNum == null || hourNum == null || month !== "*") return null;

  if (dom === "*" && dow === "*") {
    return { ...DEFAULT_PARSED, kind: "daily", minute: minuteNum, hour: hourNum };
  }
  if (dom === "*" && /^\d$/.test(dow)) {
    return { ...DEFAULT_PARSED, kind: "weekly", minute: minuteNum, hour: hourNum, dayOfWeek: parseInt(dow, 10) };
  }
  if (dow === "*" && /^\d{1,2}$/.test(dom) && parseInt(dom, 10) >= 1 && parseInt(dom, 10) <= 28) {
    return { ...DEFAULT_PARSED, kind: "monthly", minute: minuteNum, hour: hourNum, dayOfMonth: parseInt(dom, 10) };
  }
  return null;
}

function buildCron(parsed: ParsedCron): string {
  switch (parsed.kind) {
    case "minutes":
      return `*/${parsed.minuteInterval} * * * *`;
    case "hourly":
      return "0 * * * *";
    case "daily":
      return `${parsed.minute} ${parsed.hour} * * *`;
    case "weekly":
      return `${parsed.minute} ${parsed.hour} * * ${parsed.dayOfWeek}`;
    case "monthly":
      return `${parsed.minute} ${parsed.hour} ${parsed.dayOfMonth} * *`;
    default:
      return "0 0 * * *";
  }
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export function RepeatIntervalPicker({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (cron: string) => void;
  disabled?: boolean;
}) {
  const parsed = useMemo(() => parseCron(value), [value]);
  const [forceCustom, setForceCustom] = useState(false);
  const isCustom = forceCustom || !parsed;
  const effective = parsed ?? DEFAULT_PARSED;

  const emit = (patch: Partial<ParsedCron>) => {
    onChange(buildCron({ ...effective, ...patch }));
  };

  return (
    <div className={cn("space-y-1.5", disabled && "opacity-50")}>
      <div className="flex flex-wrap items-center gap-1.5">
        <select
          disabled={disabled}
          value={isCustom ? "custom" : effective.kind}
          onChange={(e) => {
            const next = e.target.value;
            if (next === "custom") {
              setForceCustom(true);
              return;
            }
            setForceCustom(false);
            emit({ kind: next as RepeatKind });
          }}
          className="rounded-md border bg-background px-2 py-1.5 text-[12px] disabled:opacity-50"
        >
          <option value="minutes">Every N minutes</option>
          <option value="hourly">Hourly</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
          <option value="monthly">Monthly</option>
          <option value="custom">Custom</option>
        </select>

        {!isCustom && effective.kind === "minutes" ? (
          <select
            disabled={disabled}
            value={effective.minuteInterval}
            onChange={(e) => emit({ minuteInterval: parseInt(e.target.value, 10) })}
            className="rounded-md border bg-background px-2 py-1.5 text-[12px] disabled:opacity-50"
          >
            {MINUTE_OPTIONS.map((n) => (
              <option key={n} value={n}>
                every {n} min
              </option>
            ))}
          </select>
        ) : null}

        {!isCustom && (effective.kind === "daily" || effective.kind === "weekly" || effective.kind === "monthly") ? (
          <>
            <select
              disabled={disabled}
              value={effective.hour}
              onChange={(e) => emit({ hour: parseInt(e.target.value, 10) })}
              className="rounded-md border bg-background px-2 py-1.5 font-mono text-[12px] disabled:opacity-50"
            >
              {Array.from({ length: 24 }, (_, h) => (
                <option key={h} value={h}>
                  {pad2(h)}h
                </option>
              ))}
            </select>
            <select
              disabled={disabled}
              value={effective.minute}
              onChange={(e) => emit({ minute: parseInt(e.target.value, 10) })}
              className="rounded-md border bg-background px-2 py-1.5 font-mono text-[12px] disabled:opacity-50"
            >
              {[0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55].map((m) => (
                <option key={m} value={m}>
                  {pad2(m)}m
                </option>
              ))}
            </select>
          </>
        ) : null}

        {!isCustom && effective.kind === "weekly" ? (
          <select
            disabled={disabled}
            value={effective.dayOfWeek}
            onChange={(e) => emit({ dayOfWeek: parseInt(e.target.value, 10) })}
            className="rounded-md border bg-background px-2 py-1.5 text-[12px] disabled:opacity-50"
          >
            {DOW_LABELS.map((label, idx) => (
              <option key={label} value={idx}>
                {label}
              </option>
            ))}
          </select>
        ) : null}

        {!isCustom && effective.kind === "monthly" ? (
          <select
            disabled={disabled}
            value={effective.dayOfMonth}
            onChange={(e) => emit({ dayOfMonth: parseInt(e.target.value, 10) })}
            className="rounded-md border bg-background px-2 py-1.5 text-[12px] disabled:opacity-50"
          >
            {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => (
              <option key={d} value={d}>
                day {d}
              </option>
            ))}
          </select>
        ) : null}
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
