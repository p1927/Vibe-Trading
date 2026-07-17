import type { PipelineLogEntry } from "@/lib/api";

export function parseLogTimestamp(entry: PipelineLogEntry | undefined): number {
  if (!entry?.at) return 0;
  const ms = Date.parse(entry.at);
  return Number.isFinite(ms) ? ms : 0;
}

export function newestLogTimestamp(entries: PipelineLogEntry[] | undefined): number {
  if (!entries?.length) return 0;
  return entries.reduce((max, row) => Math.max(max, parseLogTimestamp(row)), 0);
}

export function artifactLogMatchesAsOf(
  entries: PipelineLogEntry[] | undefined,
  asOf: string | undefined,
  skewMs = 120_000,
): boolean {
  if (!entries?.length || !asOf) return false;
  const asOfMs = Date.parse(asOf);
  if (!Number.isFinite(asOfMs)) return false;
  const newest = newestLogTimestamp(entries);
  if (!newest) return false;
  return Math.abs(newest - asOfMs) <= skewMs;
}

export function pickDisplayPipelineLogs(
  logs: PipelineLogEntry[],
  artifactLog: PipelineLogEntry[] | undefined,
  running: boolean,
  artifactAsOf?: string,
): PipelineLogEntry[] {
  if (running) return logs;
  if (logs.length) return logs;
  if (!artifactLog?.length) return [];
  if (artifactLogMatchesAsOf(artifactLog, artifactAsOf)) return artifactLog;
  return [];
}

export function mergePipelineLogs(
  prev: PipelineLogEntry[],
  incoming: PipelineLogEntry[] | undefined,
): PipelineLogEntry[] {
  if (!incoming?.length) return prev;
  if (!prev.length) return incoming;
  const prevNewest = newestLogTimestamp(prev);
  const incomingNewest = newestLogTimestamp(incoming);
  if (incomingNewest >= prevNewest) return incoming;
  return prev;
}

export function formatPipelineLogTime(at: string | undefined): string {
  if (!at) return "";
  return new Date(at).toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function pipelineLogRowKey(entry: PipelineLogEntry, idx: number): string {
  return `${entry.at ?? ""}|${entry.stage}|${entry.message}|${idx}`;
}
