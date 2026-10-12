export interface PricePoint {
  date: string;
  close: number;
}

function isoDay(raw: string | undefined): string {
  return String(raw ?? "").slice(0, 10);
}

export function mergePriceSeries(
  sources: Array<Array<{ date?: string; close?: number | null }>>,
): PricePoint[] {
  const map = new Map<string, number>();
  for (const src of sources) {
    for (const p of src) {
      const d = isoDay(p.date);
      const c = Number(p.close);
      if (d && Number.isFinite(c)) map.set(d, c);
    }
  }
  return [...map.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([date, close]) => ({ date, close }));
}
