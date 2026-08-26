/** Small formatters shared between `AdvisoryBoard.tsx`'s `CandidateCard` and
 * `AdvisoryCandidateDetailModal.tsx` — kept in one place so the two surfaces never
 * silently drift in how they render the same numbers. */

export function fmtInr(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
}

/** For fraction-scale values (0-1), e.g. confidence. */
export function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(0)}%`;
}

/** For already-percent-scale values (e.g. `expected_move_pct`, which is `1.2` meaning
 * 1.2%, not a 0-1 fraction) — no ×100. */
export function fmtPctScale(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v.toFixed(2)}%`;
}

export function fmtRatio(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v.toFixed(2)}:1`;
}

export function toneFor(v: number | null | undefined): "up" | "down" | "neutral" {
  if (v == null || !Number.isFinite(v)) return "neutral";
  return v >= 0 ? "up" : "down";
}
