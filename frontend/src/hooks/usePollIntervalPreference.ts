import { useEffect, useState } from "react";
import { isValidPollMs } from "@/lib/pollIntervalOptions";

export function usePollIntervalPreference(storageKey: string, defaultMs: number) {
  const [pollMs, setPollMsState] = useState(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw == null) return defaultMs;
      const parsed = Number(raw);
      return isValidPollMs(parsed) ? parsed : defaultMs;
    } catch {
      return defaultMs;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, String(pollMs));
    } catch {
      /* ignore */
    }
  }, [pollMs, storageKey]);

  const setPollMs = (ms: number) => {
    setPollMsState(isValidPollMs(ms) ? ms : defaultMs);
  };

  return { pollMs, setPollMs };
}
