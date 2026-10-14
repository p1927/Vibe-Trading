import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  api,
  type IndexSimulationResult,
  type SimulateIndexPredictionRequest,
} from "@/lib/api";

/**
 * The one debounced what-if simulation (D306: CausalFactorExplorer and FactorImpactWorkbench each had a copy).
 * `request === null` is idle (no shock / no event chosen): the result and error are cleared. A response for a
 * request that has since been replaced is dropped, so dragging the slider can never leave an older shock's
 * numbers on screen. `onResult` is told about every change of the shown result (including the clear).
 */
export function useIndexSimulation(
  request: SimulateIndexPredictionRequest | null,
  opts: { delayMs?: number; onResult?: (result: IndexSimulationResult | null) => void } = {},
) {
  const { delayMs = 300 } = opts;
  const [simulation, setSimulation] = useState<IndexSimulationResult | null>(null);
  const [simulating, setSimulating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seqRef = useRef(0);
  const onResultRef = useRef(opts.onResult);
  onResultRef.current = opts.onResult;
  const key = JSON.stringify(request);

  useEffect(() => {
    const seq = ++seqRef.current; // also invalidates any request still in flight
    const show = (result: IndexSimulationResult | null, message: string | null) => {
      setSimulation(result);
      setError(message);
      onResultRef.current?.(result);
    };
    if (!request) {
      setSimulating(false);
      show(null, null);
      return;
    }
    const timer = window.setTimeout(async () => {
      setSimulating(true);
      setError(null);
      try {
        const res = await api.simulateIndexPrediction(request);
        if (seq !== seqRef.current) return;
        if (res.status === "error" || res.status === "not_found" || !res.simulation) {
          // the previous what-if no longer matches the controls: not drawn
          show(null, res.message || (res.simulation ? "Simulation failed" : "Simulation returned no result"));
        } else {
          show(res.simulation, null);
        }
      } catch (err) {
        if (seq !== seqRef.current) return;
        const msg = err instanceof Error ? err.message : "Simulation request failed";
        show(null, err instanceof ApiError && (err.status === 404 || err.status === 405)
          ? `${msg} — restart trade API on port 8899.`
          : msg);
      } finally {
        if (seq === seqRef.current) setSimulating(false);
      }
    }, delayMs);
    return () => window.clearTimeout(timer);
    // `key` is the request's value; `request` itself is a fresh object every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, delayMs]);

  return { simulation, simulating, error };
}
