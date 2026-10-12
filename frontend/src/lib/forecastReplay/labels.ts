// Which chart markers get their text. Pure: bar indexes in, booleans out (the dots are always drawn).

/** Bars a label occupies at this bar spacing (`labelPx` is about "+12.34%" at the marker font). */
export function labelGapBars(barSpacing: number, labelPx = 48): number {
  return barSpacing > 0 ? Math.max(1, Math.ceil(labelPx / barSpacing)) : 1;
}

/**
 * Labels from left to right, each kept only when it is `minBars` or more bars from the last kept one. `priority`
 * items (the anchor) are always kept and push their neighbours out. Ties on one bar keep the earlier item.
 */
export function thinLabels(items: { idx: number; priority?: boolean }[], minBars: number): boolean[] {
  const keep = items.map(() => false);
  const kept: number[] = [];
  const far = (idx: number) => kept.every((k) => Math.abs(k - idx) >= minBars);
  items.forEach((it, i) => { if (it.priority) { keep[i] = true; kept.push(it.idx); } });
  [...items.keys()]
    .filter((i) => !items[i].priority)
    .sort((a, b) => items[a].idx - items[b].idx || a - b)
    .forEach((i) => { if (far(items[i].idx)) { keep[i] = true; kept.push(items[i].idx); } });
  return keep;
}
