import type { BacktestMetrics } from "@/lib/api";

// A cell that starts with = + - @ (or a tab/CR) is a formula when the file is opened in a spreadsheet, and the
// text here (a trade's reason, a symbol) is model/data text. A real number like -5.25 is left alone.
const FORMULA_START = /^[=+\-@\t\r]/;

function isPlainNumber(text: string): boolean {
  return text.trim() !== "" && !/^[\t\r]/.test(text) && Number.isFinite(Number(text));
}

export function escapeCsvField(value: unknown): string {
  let str = String(value ?? "");
  if (FORMULA_START.test(str) && !isPlainNumber(str)) str = `'${str}`;
  if (str.includes(",") || str.includes('"') || str.includes("\n") || str.includes("\r")) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

export function buildTradesCsv(trades: Array<Record<string, string>>): string {
  if (trades.length === 0) return "";
  const keys = [...new Set(trades.flatMap(Object.keys))];
  const header = keys.map(escapeCsvField).join(",");
  const rows = trades.map((tr) => keys.map((k) => escapeCsvField(tr[k])).join(","));
  return [header, ...rows].join("\n");
}

export function buildMetricsCsv(metrics: BacktestMetrics): string {
  const header = "metric,value";
  const rows = Object.entries(metrics).map(([k, v]) => `${escapeCsvField(k)},${escapeCsvField(v)}`);
  return [header, ...rows].join("\n");
}
