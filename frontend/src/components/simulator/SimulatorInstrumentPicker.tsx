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

/**
 * Single multi-select dropdown covering every instrument the recorder can
 * capture: the indices (NIFTY / BANKNIFTY / SENSEX) plus every NIFTY 50
 * equity constituent, grouped by sector. Replaces the old split UI (index
 * checkboxes + a separate equities-only picker) so "what to record" lives
 * in one place.
 */
export function SimulatorInstrumentPicker({
  indices,
  selectedIndices,
  onChangeIndices,
  selectedEquities,
  onChangeEquities,
  disabled,
}: {
  indices: string[];
  selectedIndices: string[];
  onChangeIndices: (symbols: string[]) => void;
  selectedEquities: string[];
  onChangeEquities: (symbols: string[]) => void;
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

  const sectors = useMemo(() => {
    const q = filter.trim().toUpperCase();
    const bySector = new Map<string, ConstituentInfo[]>();
    for (const c of constituents) {
      if (q && !c.symbol.includes(q) && !c.name.toUpperCase().includes(q) && !c.sector.toUpperCase().includes(q)) {
        continue;
      }
      const sector = c.sector || "Other";
      if (!bySector.has(sector)) bySector.set(sector, []);
      bySector.get(sector)!.push(c);
    }
    return [...bySector.entries()]
      .map(([sector, items]) => [sector, [...items].sort((a, b) => a.symbol.localeCompare(b.symbol))] as const)
      .sort((a, b) => a[0].localeCompare(b[0]));
  }, [constituents, filter]);

  const filteredIndices = useMemo(() => {
    const q = filter.trim().toUpperCase();
    if (!q) return indices;
    return indices.filter((u) => u.includes(q));
  }, [indices, filter]);

  const toggleIndex = (symbol: string) => {
    onChangeIndices(
      selectedIndices.includes(symbol)
        ? selectedIndices.filter((s) => s !== symbol)
        : [...selectedIndices, symbol],
    );
  };

  const toggleEquity = (symbol: string) => {
    onChangeEquities(
      selectedEquities.includes(symbol)
        ? selectedEquities.filter((s) => s !== symbol)
        : [...selectedEquities, symbol],
    );
  };

  const toggleSector = (items: ConstituentInfo[]) => {
    const symbols = items.map((c) => c.symbol);
    const allSelected = symbols.every((s) => selectedEquities.includes(s));
    if (allSelected) {
      onChangeEquities(selectedEquities.filter((s) => !symbols.includes(s)));
    } else {
      const merged = new Set([...selectedEquities, ...symbols]);
      onChangeEquities([...merged]);
    }
  };

  const selectAllNifty50 = () => {
    onChangeEquities(constituents.map((c) => c.symbol));
  };

  const clearAll = () => {
    onChangeIndices([]);
    onChangeEquities([]);
  };

  const totalSelected = selectedIndices.length + selectedEquities.length;

  return (
    <div className="w-full max-w-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        className="inline-flex h-8 items-center gap-1.5 rounded-lg border bg-background px-2.5 text-xs hover:bg-muted/50 disabled:opacity-50"
        data-testid="instrument-picker-toggle"
      >
        Instruments
        {totalSelected > 0 ? (
          <span className="rounded-full bg-primary/15 px-1.5 py-0.5 text-[10px] font-medium text-primary">
            {totalSelected}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="mt-2 w-80 rounded-lg border bg-background/95 p-2 shadow-sm">
          {error ? <p className="mb-2 text-[11px] text-destructive">{error}</p> : null}
          <div className="mb-2 flex items-center gap-1.5 rounded border bg-muted/30 px-2 py-1">
            <Search className="h-3 w-3 text-muted-foreground" />
            <input
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter symbols, sectors…"
              disabled={disabled}
              className="w-full bg-transparent text-xs outline-none"
              data-testid="instrument-picker-filter"
            />
          </div>

          <div className="mb-2 flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={selectAllNifty50}
              disabled={disabled || constituents.length === 0}
              className="rounded border bg-background px-1.5 py-0.5 text-[10px] font-medium hover:bg-muted/50 disabled:opacity-50"
              data-testid="instrument-picker-select-all-nifty50"
            >
              Select all NIFTY 50 constituents
            </button>
            <button
              type="button"
              onClick={clearAll}
              disabled={disabled || totalSelected === 0}
              className="rounded border bg-background px-1.5 py-0.5 text-[10px] font-medium hover:bg-muted/50 disabled:opacity-50"
            >
              Clear all
            </button>
          </div>

          <div className="max-h-80 overflow-auto">
            {filteredIndices.length > 0 ? (
              <div className="mb-2">
                <p className="px-1 py-0.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                  Indices
                </p>
                <div className="grid grid-cols-2 gap-x-2 gap-y-1">
                  {filteredIndices.map((u) => (
                    <label
                      key={u}
                      className={cn(
                        "flex items-center gap-1.5 rounded px-1 py-0.5 text-[11px]",
                        selectedIndices.includes(u) && "bg-primary/10",
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={selectedIndices.includes(u)}
                        disabled={disabled}
                        onChange={() => toggleIndex(u)}
                        className="h-3 w-3 rounded border-border"
                      />
                      <span className="truncate">{u}</span>
                    </label>
                  ))}
                </div>
              </div>
            ) : null}

            {sectors.length === 0 && constituents.length === 0 ? (
              <p className="px-1 py-2 text-[11px] text-muted-foreground">Loading…</p>
            ) : (
              sectors.map(([sector, items]) => {
                const allSelected = items.every((c) => selectedEquities.includes(c.symbol));
                return (
                  <div key={sector} className="mb-2">
                    <label className="flex cursor-pointer items-center gap-1.5 px-1 py-0.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground hover:text-foreground">
                      <input
                        type="checkbox"
                        checked={allSelected}
                        disabled={disabled}
                        onChange={() => toggleSector(items)}
                        className="h-3 w-3 rounded border-border"
                      />
                      {sector}
                    </label>
                    <div className="grid grid-cols-2 gap-x-2 gap-y-1">
                      {items.map((c) => (
                        <label
                          key={c.symbol}
                          className={cn(
                            "flex items-center gap-1.5 rounded px-1 py-0.5 text-[11px]",
                            selectedEquities.includes(c.symbol) && "bg-primary/10",
                          )}
                          title={c.name}
                        >
                          <input
                            type="checkbox"
                            checked={selectedEquities.includes(c.symbol)}
                            disabled={disabled}
                            onChange={() => toggleEquity(c.symbol)}
                            className="h-3 w-3 rounded border-border"
                          />
                          <span className="truncate">{c.symbol}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                );
              })
            )}
            {sectors.length === 0 && constituents.length > 0 ? (
              <p className="px-1 py-2 text-[11px] text-muted-foreground">No matches.</p>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
