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
