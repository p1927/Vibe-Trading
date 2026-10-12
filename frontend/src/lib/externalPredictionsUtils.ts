import type { ExternalPredictionRecord, ExternalPredictionSnapshot, ExternalPredictionSource } from "@/lib/api";

export interface StreetSummaryStats {
  horizonDays: number;
  watchlistCount: number;
  /** Sources whose forecast counts at this horizon (the server's `horizon_fit === true`: a stated date inside the window). */
  countedCount: number;
  targetMin: number | null;
  targetMax: number | null;
  targetMedian: number | null;
  /** Targets of records flagged as a horizon mismatch (e.g. a year-out target): listed apart, not in the range. */
  longHorizon: { sourceId: string; mid: number }[];
  /** Records stating no target date (server `horizon_fit == null`, D398): no evidence of their horizon, listed apart. */
  undated: { sourceId: string; mid: number }[];
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

const SYNDICATION_DOMAINS = new Set([
  "economictimes.indiatimes.com",
  "economictimes.com",
  "moneycontrol.com",
  "livemint.com",
]);

/** Mirror backend primary_domain — native publisher host, not syndication fallback. */
export function primarySourceDomain(source: ExternalPredictionSource | undefined): string {
  if (!source?.domains?.length) return "";
  for (const domain of source.domains) {
    const d = normalizeDomain(domain);
    if (d && !SYNDICATION_DOMAINS.has(d)) return d;
  }
  return normalizeDomain(source.domains[0] ?? "");
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

/** The server's verdict (`horizon_fit`, validators.horizon_in_window, the same rule as the external_consensus track):
 * false = a stated date outside the horizon window. This file holds no horizon rule of its own. */
export function hasHorizonMismatch(record: ExternalPredictionRecord): boolean {
  return record.horizon_fit === false;
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

/** Sources that failed refresh or have no usable forecast — shown outside ok cards. */
export function filterIssuePredictions(
  predictions: ExternalPredictionRecord[] | undefined,
): ExternalPredictionRecord[] {
  return (predictions ?? []).filter((p) => {
    if (p.fetch_status === "ok" && p.target?.mid != null) return false;
    return p.fetch_status === "error" || p.fetch_status === "not_found";
  });
}

/** Prefer refresh completion time for UI; fall back to batch start / cache time. */
export function snapshotDisplayUpdatedAt(snapshot: ExternalPredictionSnapshot | null | undefined): string | null {
  const completed = snapshot?.refresh_completed_at?.trim();
  if (completed) return completed;
  const fetched = snapshot?.fetched_at?.trim();
  return fetched || null;
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

const FORECAST_SECTION_LABELS: Record<string, string> = {
  next_week_table: "Next week support/resistance table",
  next_month_table: "Next month support/resistance table",
  range_pattern: "Quoted range",
  level_pattern: "Numeric levels in article",
};

export function formatForecastSection(section: string | undefined): string | null {
  if (!section?.trim()) return null;
  return FORECAST_SECTION_LABELS[section] ?? section.replace(/_/g, " ");
}

export function formatForecastProvenance(record: ExternalPredictionRecord): string | null {
  const prov = record.provenance;
  if (!prov) return null;
  const parts: string[] = [];
  const section =
    formatForecastSection(prov.forecast_section as string | undefined) ||
    formatForecastSection(prov.regex_style as string | undefined);
  if (section) parts.push(section);
  if (prov.forecast_heading?.trim()) {
    parts.push(prov.forecast_heading.trim().slice(0, 120));
  } else if (prov.horizon_window?.trim()) {
    parts.push(`Window ${prov.horizon_window.trim()}`);
  }
  return parts.length ? parts.join(" · ") : null;
}

const PAGE_KIND_LABELS: Record<string, string> = {
  article: "Broker/article page",
  hub: "Forecast hub",
  listing: "Listing/topic page",
  other: "Other page",
};

const FETCH_METHOD_LABELS: Record<string, string> = {
  crawl4ai: "Crawl4AI",
  browse_agent: "Browse agent",
  path_replay: "Saved path",
  searxng_text: "SearXNG text",
};

export function formatPageKindLabel(pageKind: string | undefined): string | null {
  if (!pageKind?.trim()) return null;
  return PAGE_KIND_LABELS[pageKind] ?? pageKind.replace(/_/g, " ");
}

export function formatFetchMethodLabel(fetchMethod: string | undefined): string | null {
  if (!fetchMethod?.trim()) return null;
  return FETCH_METHOD_LABELS[fetchMethod] ?? fetchMethod.replace(/_/g, " ");
}

/** Index level, whole points, Indian grouping: the one format of every level on the Street views page. */
export function fmtLevel(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

export function computeStreetSummary(
  snapshot: ExternalPredictionSnapshot | null,
  horizonDays: number,
): StreetSummaryStats {
  const visible = filterVisiblePredictions(snapshot?.predictions);
  const withMid = visible.flatMap((p) =>
    typeof p.target?.mid === "number" && Number.isFinite(p.target.mid) ? [{ record: p, mid: p.target.mid }] : [],
  );
  // D398, decided by the server (`horizon_fit`): only a record whose stated date fits the window counts; a year-out
  // target (false) and an undated one (null/absent: never assumed to fit) are listed apart, not in range or median
  const listed = (keep: (r: ExternalPredictionRecord) => boolean) =>
    withMid.filter((x) => keep(x.record)).map((x) => ({ sourceId: x.record.source_id, mid: x.mid }));
  const mids = withMid.filter((x) => x.record.horizon_fit === true).map((x) => x.mid).sort((a, b) => a - b);
  const longHorizon = listed((r) => r.horizon_fit === false);
  const undated = listed((r) => r.horizon_fit !== true && r.horizon_fit !== false);
  const spot =
    snapshot?.internal_forecast?.spot ??
    visible.find((p) => p.spot_at_fetch != null)?.spot_at_fetch ??
    null;
  return {
    horizonDays,
    watchlistCount: snapshot?.sources?.filter((s) => s.watchlisted).length ?? 0,
    countedCount: mids.length,
    targetMin: mids.length ? mids[0] : null,
    targetMax: mids.length ? mids[mids.length - 1] : null,
    targetMedian: mids.length ? mids[Math.floor(mids.length / 2)] : null,
    longHorizon,
    undated,
    spot: typeof spot === "number" ? spot : null,
    fetchedAt: snapshotDisplayUpdatedAt(snapshot),
  };
}
