import { useEffect, useRef, useState } from "react";
import { api, errorText, type PlanContextResponse } from "@/lib/api";
import { useLatestRequest } from "@/hooks/useLatestRequest";

/**
 * The one `getPlanContext` poll (D306: the trade-plan card and the prediction page each ran their own loop).
 * - A new ticker drops the previous ticker's payload at once, and a slow answer for the
 *   previous ticker / an earlier tick never lands (latest-wins).
 * - A failed read clears the payload and sets `error`: the last "fresh" verdict is no longer current, and a
 *   failure must not read as "monitor off".
 */
export function usePlanContext(
  ticker: string | undefined,
  { pollMs, enabled = true }: { pollMs: number; enabled?: boolean },
): { context: PlanContextResponse | null; error: string | null } {
  const [context, setContext] = useState<PlanContextResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const begin = useLatestRequest();
  const shownFor = useRef(ticker);

  useEffect(() => {
    // Another ticker's payload is wrong the moment the ticker changes; a pause (enabled=false) keeps the last read.
    if (shownFor.current !== ticker) {
      shownFor.current = ticker;
      setContext(null);
      setError(null);
    }
    if (!enabled || !ticker || pollMs <= 0) return;

    const poll = async () => {
      const isCurrent = begin();
      try {
        const res = await api.getPlanContext(ticker);
        if (!isCurrent()) return;
        setContext(res);
        setError(null);
      } catch (e) {
        if (!isCurrent()) return;
        setContext(null);
        setError(errorText(e, "plan context request failed"));
      }
    };

    void poll();
    const id = window.setInterval(() => void poll(), pollMs);
    return () => {
      window.clearInterval(id);
      begin(); // a read still in flight for this ticker / settings is now stale
    };
  }, [begin, ticker, pollMs, enabled]);

  return { context, error };
}
