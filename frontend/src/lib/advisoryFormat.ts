/** Small formatters shared between `AdvisoryBoard.tsx`'s `CandidateCard` and
 * `AdvisoryCandidateDetailModal.tsx` — kept in one place so the two surfaces never
 * silently drift in how they render the same numbers. */

import { finiteOrNull } from "@/lib/finite";

export function fmtInr(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
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

/** The exit plan's headline. A missing target day / profit fraction is left out, never printed as "D+null" / "0%". */
export function fmtExitPlan(
  plan: { kind: string; target_day: number | null; target_fraction_of_max_profit: number | null },
  fractionLabel: string,
): string {
  if (plan.kind !== "target_day") return "hold to expiry";
  const day = finiteOrNull(plan.target_day);
  const fraction = finiteOrNull(plan.target_fraction_of_max_profit);
  const head = day === null ? "target day n/a" : `~D+${day}`;
  return fraction === null ? head : `${head} (${Math.round(fraction * 100)}% ${fractionLabel})`;
}
