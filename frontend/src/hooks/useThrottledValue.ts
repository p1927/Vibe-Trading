import { useLayoutEffect, useRef, useState } from "react";

/**
 * Coalesce rapid value updates to one commit per animation frame.
 * Used to limit expensive markdown re-parses during SSE token streaming.
 * Clears synchronously when value becomes empty so the stream slot unmounts cleanly.
 */
export function useThrottledValue<T>(value: T): T {
  const [throttled, setThrottled] = useState(value);
  const latestRef = useRef(value);
  const rafRef = useRef<number | null>(null);

  latestRef.current = value;

  useLayoutEffect(() => {
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }

    if (value === "" || value === null || value === undefined) {
      setThrottled(value);
      return;
    }

    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null;
      setThrottled(latestRef.current);
    });

    return () => {
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [value]);

  return throttled;
}
