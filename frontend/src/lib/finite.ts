/**
 * A finite number, or null. A missing / NaN / Infinity reading is a gap on a chart (ECharts leaves a
 * gap for `null`), never a drop to zero.
 */
export function finiteOrNull(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** The last finite value of a series, or null when there is none (for colouring by the final reading). */
export function lastFinite(values: Array<number | null>): number | null {
  for (let i = values.length - 1; i >= 0; i -= 1) {
    const v = values[i];
    if (v !== null && Number.isFinite(v)) return v;
  }
  return null;
}

/** `toFixed(digits)` of a finite reading, or an em dash for a missing one (never "0.000": Number(null) is 0). */
export function fmtFinite(value: unknown, digits = 3): string {
  const n = finiteOrNull(value);
  return n === null ? "\u2014" : n.toFixed(digits);
}
