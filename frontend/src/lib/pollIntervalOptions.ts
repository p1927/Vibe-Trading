export const WATCHERS_POLL_STORAGE_KEY = "vibe-watchers-poll-ms";

export const WATCHERS_DEFAULT_POLL_MS = 10_000;

export const WATCHERS_POLL_OPTIONS = [
  { label: "Off", ms: 0 },
  { label: "5 sec", ms: 5_000 },
  { label: "10 sec", ms: 10_000 },
  { label: "30 sec", ms: 30_000 },
  { label: "1 min", ms: 60_000 },
] as const;

export type PollIntervalOption = (typeof WATCHERS_POLL_OPTIONS)[number];

export function isValidPollMs(ms: number): boolean {
  return WATCHERS_POLL_OPTIONS.some((o) => o.ms === ms);
}
