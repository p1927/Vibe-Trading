import { useEffect, useMemo, useState } from "react";
import { Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type ConstituentInfo } from "@/lib/api";

function friendlyError(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  if (/failed to fetch|networkerror|load failed/i.test(message)) {
    return "Can't reach the recording API to load constituents.";
  }
  return message;
}

export function SimulatorEquityPicker({
  selected,
  onChange,
  disabled,
}: {
  selected: string[];
  onChange: (symbols: string[]) => void;
  disabled: boolean;
}) {
  const [constituents, setConstituents] = useState<ConstituentInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    api
      .getRecordingConstituents()
      .then((res) => setConstituents(res.constituents || []))
      .catch((err) => setError(friendlyError(err)));
  }, []);

  const filtered = useMemo(() => {
    const q = filter.trim().toUpperCase();
    if (!q) return constituents;
    return constituents.filter(
      (c) => c.symbol.includes(q) || c.name.toUpperCase().includes(q),
    );
  }, [constituents, filter]);

  const toggle = (symbol: string) => {
    onChange(
      selected.includes(symbol) ? selected.filter((s) => s !== symbol) : [...selected, symbol],
    );
  };

  return (
    <div className="w-full max-w-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        className="inline-flex h-8 items-center gap-1.5 rounded-lg border bg-background px-2.5 text-xs hover:bg-muted/50 disabled:opacity-50"
        data-testid="equity-picker-toggle"
      >
        Equities
        {selected.length > 0 ? (
          <span className="rounded-full bg-primary/15 px-1.5 py-0.5 text-[10px] font-medium text-primary">
            {selected.length}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="mt-2 rounded-lg border bg-background/95 p-2 shadow-sm">
          {error ? <p className="mb-2 text-[11px] text-destructive">{error}</p> : null}
          <div className="mb-2 flex items-center gap-1.5 rounded border bg-muted/30 px-2 py-1">
            <Search className="h-3 w-3 text-muted-foreground" />
            <input
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter NIFTY50 symbols…"
              disabled={disabled}
              className="w-full bg-transparent text-xs outline-none"
              data-testid="equity-picker-filter"
            />
          </div>
          <div className="max-h-56 overflow-auto">
            {filtered.length === 0 ? (
              <p className="px-1 py-2 text-[11px] text-muted-foreground">
                {constituents.length === 0 ? "Loading…" : "No matches."}
              </p>
            ) : (
              <div className="grid grid-cols-2 gap-x-2 gap-y-1">
                {filtered.map((c) => (
                  <label
                    key={c.symbol}
                    className={cn(
                      "flex items-center gap-1.5 rounded px-1 py-0.5 text-[11px]",
                      selected.includes(c.symbol) && "bg-primary/10",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={selected.includes(c.symbol)}
                      disabled={disabled}
                      onChange={() => toggle(c.symbol)}
                      className="h-3 w-3 rounded border-border"
                    />
                    <span className="truncate">{c.symbol}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
