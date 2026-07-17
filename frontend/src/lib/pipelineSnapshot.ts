/** Match backend normalize_as_of (second-precision UTC ISO) for stale checks. */
export function normalizePipelineAsOf(value: string | null | undefined): string {
  if (!value) return "";
  const raw = value.trim();
  if (!raw) return "";
  try {
    const d = new Date(raw.includes("T") ? raw : `${raw}T00:00:00.000Z`);
    if (Number.isNaN(d.getTime())) return raw.slice(0, 19);
    return d.toISOString().slice(0, 19);
  } catch {
    return raw.slice(0, 19);
  }
}

export function pipelineAsOfMatches(a: string | null | undefined, b: string | null | undefined): boolean {
  return normalizePipelineAsOf(a) === normalizePipelineAsOf(b);
}
