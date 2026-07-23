import type { ExternalPredictionRecord, ExternalPredictionSnapshot, ExternalPredictionSource } from "@/lib/api";
import type { LiveForecastInput } from "@/lib/forecastReplayUtils";

export interface StreetSummaryStats {
  horizonDays: number;
  watchlistCount: number;
  forecastCount: number;
  targetMin: number | null;
  targetMax: number | null;
  targetMedian: number | null;
  spot: number | null;
  fetchedAt: string | null;
}

type HorizonMatchProvenance = {
  selected_days?: number;
  target_days_ahead?: number | null;
  in_window?: boolean | null;
  soft_mismatch?: boolean;
};

export interface AddSourcePayload {
  display_name: string;
  domains: string[];
  entry_urls: string[];
  id?: string;
  kind?: string;
}

export interface AddSourceValidationResult {
  ok: boolean;
  error?: string;
  payload?: AddSourcePayload;
}

export function normalizeDomain(raw: string): string {
  let text = String(raw || "").trim().toLowerCase();
  text = text.replace(/^https?:\/\//, "");
  if (text.startsWith("www.")) text = text.slice(4);
  return text.split("/")[0]?.trim() ?? "";
}

function hostMatchesDomains(host: string, domains: string[]): boolean {
  const hostNorm = host.toLowerCase().replace(/^www\./, "");
  return domains.some((domain) => {
    const d = normalizeDomain(domain);
    if (!d) return false;
    return hostNorm === d || hostNorm.endsWith(`.${d}`);
  });
}

export function parseMultilineList(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

/** Mirror backend ``validate_user_source_request`` for add-site forms. */
export function validateAddSourceRequest(input: {
  displayName: string;
  domains: string[];
  entryUrls: string[];
  requireEntryUrls?: boolean;
  id?: string;
  kind?: string;
}): AddSourceValidationResult {
  const name = String(input.displayName || "").trim();
  if (!name) return { ok: false, error: "Display name is required." };

  const domainList = [...new Set(input.domains.map(normalizeDomain).filter(Boolean))];
  if (!domainList.length) return { ok: false, error: "At least one domain is required." };

  const urlList: string[] = [];
  for (const raw of input.entryUrls) {
    const url = String(raw || "").trim();
    if (!url) continue;
    try {
      const parsed = new URL(url);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        return { ok: false, error: `Invalid entry URL: ${url}` };
      }
      const host = parsed.hostname.toLowerCase();
      if (!host) {
        return { ok: false, error: `Invalid entry URL: ${url}` };
      }
      if (!hostMatchesDomains(host, domainList)) {
        return { ok: false, error: `Entry URL must match domain ${domainList[0]}: ${url}` };
      }
      urlList.push(url);
    } catch {
      return { ok: false, error: `Invalid entry URL: ${url}` };
    }
  }
  const uniqueUrls = [...new Set(urlList)];
  const requireEntryUrls = input.requireEntryUrls ?? !(input.id && String(input.id).trim());
  if (requireEntryUrls && !uniqueUrls.length) {
    return { ok: false, error: "At least one entry URL is required (one per line)." };
  }

  return {
    ok: true,
    payload: {
      display_name: name,
      domains: domainList,
      entry_urls: uniqueUrls,
      id: input.id?.trim() || undefined,
      kind: input.kind,
    },
  };
}

export function candidateNeedsEntryUrls(candidate: Record<string, unknown>): boolean {
  const urls = candidate.entry_urls;
  if (Array.isArray(urls) && urls.some((u) => String(u || "").trim())) return false;
  return true;
}

export function buildAddSourcePayload(
  candidate: Record<string, unknown>,
  entryUrlsOverride?: string[],
): AddSourceValidationResult {
  const domains = candidate.domains
    ? (candidate.domains as string[])
    : candidate.domain
      ? [String(candidate.domain)]
      : [];
  const entry_urls =
    entryUrlsOverride ??
    (Array.isArray(candidate.entry_urls)
      ? (candidate.entry_urls as string[]).map((u) => String(u))
      : []);
  return validateAddSourceRequest({
    displayName: String(candidate.display_name ?? candidate.domain ?? "Source"),
    domains,
    entryUrls: entry_urls,
    id: candidate.id ? String(candidate.id) : undefined,
    kind: candidate.kind ? String(candidate.kind) : undefined,
    requireEntryUrls: entryUrlsOverride !== undefined ? true : undefined,
  });
}

function horizonMatch(record: ExternalPredictionRecord): HorizonMatchProvenance | undefined {
  return record.provenance?.horizon_match as HorizonMatchProvenance | undefined;
}

export function hasHorizonMismatch(record: ExternalPredictionRecord): boolean {
  const match = horizonMatch(record);
  if (!match) return false;
  return match.soft_mismatch === true || match.in_window === false;
}

/** Use article target horizon on chart when tab horizon differs (soft mismatch). */
export function calendarDaysBetween(startIso: string, endIso: string): number {
  const start = new Date(`${startIso.slice(0, 10)}T12:00:00Z`);
  const end = new Date(`${endIso.slice(0, 10)}T12:00:00Z`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return 0;
  const ms = end.getTime() - start.getTime();
  return Math.max(0, Math.round(ms / 86_400_000));
}

export function effectiveChartHorizonDays(
  record: ExternalPredictionRecord,
  tabHorizonDays: number,
): number {
  const targetDate = record.target_date?.slice(0, 10);
  const anchor = record.as_of?.slice(0, 10) || record.published_at?.slice(0, 10);
  if (hasHorizonMismatch(record) && targetDate && anchor) {
    const fromTargetDate = calendarDaysBetween(anchor, targetDate);
    if (fromTargetDate > 0) return fromTargetDate;
  }
  if (!hasHorizonMismatch(record)) return tabHorizonDays;
  const ahead = horizonMatch(record)?.target_days_ahead;
  if (typeof ahead === "number" && ahead > 0) return Math.max(1, Math.round(ahead));
  return tabHorizonDays;
}

export function canApproveNavigationPath(
  source: ExternalPredictionSource | undefined,
  horizonDays: number,
): boolean {
  if (!source) return false;
  const key = String(horizonDays);
  const saved = source.saved_paths?.[key];
  if (!saved || saved.stale) return false;
  const approved = source.approved_paths?.[key];
  return approved?.approved_by !== "user";
}

export function filterVisiblePredictions(
  predictions: ExternalPredictionRecord[] | undefined,
): ExternalPredictionRecord[] {
  return (predictions ?? []).filter(
    (p) => p.fetch_status === "ok" && p.target?.mid != null,
  );
}

export function recordToLiveForecast(record: ExternalPredictionRecord): LiveForecastInput | undefined {
  const spot = record.spot_at_fetch;
  const mid = record.target?.mid;
  if (spot == null || mid == null || spot <= 0) return undefined;
  const expectedReturnPct =
    record.expected_return_pct ?? Math.round((mid / spot - 1) * 10000) / 100;
  return {
    asOf: record.as_of,
    spot,
    expectedReturnPct,
    rangeLow: record.target?.low ?? null,
    rangeHigh: record.target?.high ?? null,
  };
}

export function formatHorizonMatch(record: ExternalPredictionRecord): string | null {
  const match = horizonMatch(record);
  if (!match) return null;
  const selected = match.selected_days ?? record.horizon_days;
  const ahead = match.target_days_ahead;
  const base =
    ahead == null ? `Selected ${selected}d horizon` : `Selected ${selected}d · Target ~${ahead}d ahead`;
  if (hasHorizonMismatch(record)) {
    return `${base} · Horizon mismatch (chart uses article target date)`;
  }
  return base;
}

export function computeStreetSummary(
  snapshot: ExternalPredictionSnapshot | null,
  horizonDays: number,
): StreetSummaryStats {
  const visible = filterVisiblePredictions(snapshot?.predictions);
  const mids = visible
    .map((p) => p.target?.mid)
    .filter((v): v is number => typeof v === "number" && Number.isFinite(v))
    .sort((a, b) => a - b);
  const spot =
    snapshot?.internal_forecast?.spot ??
    visible.find((p) => p.spot_at_fetch != null)?.spot_at_fetch ??
    null;
  return {
    horizonDays,
    watchlistCount: snapshot?.sources?.filter((s) => s.watchlisted).length ?? 0,
    forecastCount: visible.length,
    targetMin: mids.length ? mids[0] : null,
    targetMax: mids.length ? mids[mids.length - 1] : null,
    targetMedian: mids.length ? mids[Math.floor(mids.length / 2)] : null,
    spot: typeof spot === "number" ? spot : null,
    fetchedAt: snapshot?.fetched_at ?? null,
  };
}
