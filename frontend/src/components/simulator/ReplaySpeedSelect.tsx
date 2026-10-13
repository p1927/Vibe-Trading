import { REPLAY_SPEEDS } from "@/lib/simSpeed";

/** The one replay-speed picker (docs/DECISIONS.md D19: 1x, 2x or 5x only; the simulator routes
 * refuse anything else, so a free number box only produced 400s for 0, 0.5 or 60). */
export function ReplaySpeedSelect({
  value,
  onChange,
  disabled,
  className = "rounded border bg-background px-1.5 py-0.5 text-xs",
  testId,
}: {
  value: number;
  onChange: (speed: number) => void;
  disabled?: boolean;
  className?: string;
  testId?: string;
}) {
  // A speed outside the allowed set (an older armed session) stays visible rather than snapping.
  const options: number[] = (REPLAY_SPEEDS as readonly number[]).includes(value)
    ? [...REPLAY_SPEEDS]
    : [value, ...REPLAY_SPEEDS];
  return (
    <select
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      disabled={disabled}
      className={className}
      data-testid={testId}
      aria-label="Replay speed"
    >
      {options.map((s) => (
        <option key={s} value={s}>
          {s}×
        </option>
      ))}
    </select>
  );
}
