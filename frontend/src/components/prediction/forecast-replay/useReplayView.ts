import { useCallback, useEffect, useMemo, useState } from "react";
import { useForecastReplay } from "@/hooks/useForecastReplay";
import { sourceStyle, type SourceStyle } from "@/lib/forecastReplay/sourceStyle";
import type { Provenance, ReplayRecord, ReplaySource } from "@/lib/forecastReplay/types";
import { forHorizon, shiftDays } from "@/lib/forecastReplay/view";

export interface ReplayViewOptions {
  ticker: string;
  horizon: number;
  defaultSources: string[];
  defaultProvenance: Provenance[];
  windowDays?: number;
  /** Only one source may be selected at a time (scoreboard single-track mode). */
  single?: boolean;
}

/**
 * Selection state + the one fetch behind every replay view (day panel, track record). The window ends at
 * the newest day any selected source has history for, so backtest-only selections are not shown empty.
 */
export function useReplayView(o: ReplayViewOptions) {
  const [selected, setSelected] = useState<string[]>(o.defaultSources);
  const [provenance, setProvenance] = useState<Provenance[]>(o.defaultProvenance);
  const [windowDays, setWindowDays] = useState(o.windowDays ?? 90);
  const [end, setEnd] = useState(() => new Date().toISOString().slice(0, 10));

  const { bundle, loading, error, refetch } = useForecastReplay({
    ticker: o.ticker,
    sources: selected,
    start: shiftDays(end, -windowDays),
    end,
    horizon: o.horizon,
    provenance: provenance.join(","),
  });

  const allSources: ReplaySource[] = bundle?.sources ?? [];
  /** The server's provenance of a source; undefined until the first bundle lists it (nothing is guessed from the id). */
  const provOf = useCallback(
    (id: string): Provenance | undefined => allSources.find((s) => s.source_id === id)?.provenance,
    [allSources],
  );

  useEffect(() => {
    const days = allSources.filter((s) => selected.includes(s.source_id) && s.last_day).map((s) => s.last_day as string);
    const last = days.length ? days.sort()[days.length - 1] : null;
    if (last && last !== end) setEnd(last);
  }, [allSources, selected, end]);

  useEffect(() => {
    if (o.single) setSelected((s) => (s.length > 1 ? s.slice(0, 1) : s));
  }, [o.single]);

  const toggleSource = useCallback(
    (id: string) =>
      setSelected((s) => {
        if (o.single) return [id];
        if (!s.includes(id)) return [...s, id];
        return s.length > 1 ? s.filter((x) => x !== id) : s;
      }),
    [o.single],
  );

  const toggleProvenance = useCallback(
    (p: Provenance) =>
      setProvenance((cur) => {
        const next = cur.includes(p) ? cur.filter((x) => x !== p) : [...cur, p];
        // keep >= 1 provenance and >= 1 selected source that the remaining provenances can serve
        return next.length && selected.some((id) => { const q = provOf(id); return q != null && next.includes(q); }) ? next : cur;
      }),
    [selected, provOf],
  );

  const shown = useMemo(() => selected.filter((id) => { const p = provOf(id); return !p || provenance.includes(p); }), [selected, provenance, provOf]);
  const styles = useMemo(() => {
    const m: Record<string, SourceStyle> = {};
    for (const s of allSources) m[s.source_id] = sourceStyle(s.source_id, s.provenance, s.label);
    return m;
  }, [allSources]);
  /** Every loaded record of the shown sources, whatever horizons it carries (the horizon picker's source). */
  const loaded: ReplayRecord[] = useMemo(
    () => (bundle?.records ?? []).filter((r) => shown.includes(r.source_id)),
    [bundle, shown],
  );
  const records: ReplayRecord[] = useMemo(() => forHorizon(loaded, o.horizon), [loaded, o.horizon]);

  return {
    bundle, loading, error, refetch, allSources, selected: shown, provenance, windowDays, setWindowDays,
    toggleSource, toggleProvenance, styles, records, loaded, end,
  };
}
