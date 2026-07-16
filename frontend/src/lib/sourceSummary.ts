import type { ProvenanceSource } from "@/lib/api";
import { parseSourceContent } from "@/lib/sourceContent";

const GENERIC_STATUS_RE = /^status:\s*(ok|success|completed?|done|ready|running)\.?$/i;
const GENERIC_SUMMARIES = new Set(
  [
    "data retrieved",
    "hub plan loaded",
    "evidence recorded",
    "multi-agent bull/bear debate",
    "no summary available for this source.",
  ].map((s) => s.toLowerCase()),
);

export function isMeaningfulSummary(summary?: string | null): boolean {
  const text = summary?.trim();
  if (!text) return false;
  const lower = text.toLowerCase();
  if (GENERIC_SUMMARIES.has(lower)) return false;
  if (GENERIC_STATUS_RE.test(lower)) return false;
  if (lower.startsWith("status:")) {
    const statusVal = lower.slice("status:".length).trim();
    if (GENERIC_SUMMARIES.has(statusVal) || statusVal.length < 4) return false;
  }
  if (text.length >= 12) return true;
  if (/\d/.test(text)) return true;
  if (text.includes(" · ") || text.includes(", ") || text.includes(" @ ")) return true;
  return false;
}

function summaryFromParsedData(data: unknown): string | null {
  if (!data || typeof data !== "object") return null;

  if (Array.isArray(data)) {
    return data.length > 0 ? `${data.length} items` : null;
  }

  const record = data as Record<string, unknown>;
  const nested =
    record.browse_summary ??
    record.chain_snapshot ??
    record.data ??
    record.result ??
    record.response ??
    record.payload;

  const target =
    nested && typeof nested === "object" && !Array.isArray(nested)
      ? (nested as Record<string, unknown>)
      : record;

  const symbol = String(
    target.symbol ?? target.underlying ?? target.ticker ?? target.instrument ?? "",
  ).trim();
  const spot = target.spot ?? target.ltp ?? target.last_price ?? target.close ?? target.underlying_ltp;
  const expiry = String(target.expiry ?? target.expiry_date ?? target.expiration ?? "").trim();
  const atm = target.atm_strike;
  const pcr = target.pcr;
  const chain = target.chain ?? target.strikes;
  const chainRows = target.chain_rows;
  const expiries = target.expiries;

  if (symbol && spot != null && String(spot).trim()) {
    const parts = [`${symbol} @ ${spot}`];
    if (expiry) parts.push(`exp ${expiry}`);
    if (atm != null) parts.push(`ATM ${atm}`);
    if (pcr != null) parts.push(`PCR ${pcr}`);
    if (Array.isArray(chain) && chain.length) parts.push(`${chain.length} strikes`);
    else if (chainRows != null) parts.push(`${chainRows} strikes`);
    else if (Array.isArray(expiries) && expiries.length) parts.push(`${expiries.length} expiries`);
    return parts.join(" · ");
  }

  if (symbol) {
    const parts = [symbol];
    if (expiry) parts.push(`exp ${expiry}`);
    if (Array.isArray(chain) && chain.length) parts.push(`${chain.length} strikes`);
    return parts.join(" · ");
  }

  const markdown = record.markdown;
  if (typeof markdown === "string") {
    for (const line of markdown.split("\n")) {
      const cleaned = line.trim().replace(/^#+\s*/, "");
      if (!cleaned || cleaned.startsWith("|") || cleaned.startsWith("-")) continue;
      if (cleaned.startsWith("_") && cleaned.endsWith("_")) continue;
      return cleaned;
    }
  }

  const strategies = record.ranked_strategies ?? record.strategies;
  if (Array.isArray(strategies) && strategies.length) {
    const top = strategies[0];
    if (top && typeof top === "object") {
      const name = String((top as Record<string, unknown>).name ?? (top as Record<string, unknown>).strategy ?? "strategy");
      return `Top: ${name}`;
    }
  }

  const prediction = record.prediction;
  if (prediction && typeof prediction === "object") {
    const view = String((prediction as Record<string, unknown>).view ?? (prediction as Record<string, unknown>).direction ?? "").trim();
    if (view) return `View: ${view}`;
  }

  if (Array.isArray(record.results) && record.results.length) {
    return `${record.results.length} results`;
  }

  return null;
}

/** Best-effort summary for UI: prefer backend summary, else derive from raw payload. */
export function resolveSourceSummary(source: ProvenanceSource): string | null {
  const backend = source.summary?.trim();
  if (isMeaningfulSummary(backend)) return backend!;

  if (source.raw_data?.trim()) {
    const parsed = parseSourceContent(source.raw_data, source);
    const derived = summaryFromParsedData(parsed.data);
    if (isMeaningfulSummary(derived)) return derived;
    if (parsed.kind === "markdown" && isMeaningfulSummary(parsed.text)) {
      const firstLine = parsed.text
        .split("\n")
        .map((line) => line.trim().replace(/^#+\s*/, ""))
        .find((line) => line && !line.startsWith("|") && !line.startsWith("-"));
      if (isMeaningfulSummary(firstLine)) return firstLine!;
    }
    if (parsed.kind === "text" && isMeaningfulSummary(parsed.text)) {
      return parsed.text.slice(0, 180);
    }
  }

  return null;
}
