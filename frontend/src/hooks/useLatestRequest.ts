import { useCallback, useRef } from "react";

/**
 * The one latest-request-wins guard (D306: KnowledgeEngine had a private copy while every other polled or
 * parameter-driven loader had none). `begin()` returns a check that turns false once a newer request has
 * begun, so a slow older answer (previous agent / horizon / page, or an earlier poll tick) can never
 * overwrite - or clear the loading flag of - the newer one. Every `then`/`catch`/`finally` of a loader checks it.
 */
export function useLatestRequest() {
  const generation = useRef(0);
  return useCallback(() => {
    const mine = ++generation.current;
    return () => mine === generation.current;
  }, []);
}
