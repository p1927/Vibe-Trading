interface Props {
  options: number[];
  value: number;
  onChange: (h: number) => void;
}

/** Horizons actually present in the loaded records; one option = nothing to choose. */
export function HorizonSelect({ options, value, onChange }: Props) {
  return (
    <label className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
      Horizon
      <select
        value={value} disabled={options.length < 2} onChange={(e) => onChange(Number(e.target.value))}
        data-testid="horizon-select" className="rounded border bg-background px-1 py-0.5 text-foreground"
      >
        {options.map((h) => <option key={h} value={h}>{h}d</option>)}
      </select>
    </label>
  );
}
