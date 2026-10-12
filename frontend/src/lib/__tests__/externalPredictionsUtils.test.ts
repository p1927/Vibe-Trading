import {
  buildAddSourcePayload,
  candidateNeedsEntryUrls,
  computeStreetSummary,
  fmtLevel,
  filterIssuePredictions,
  filterVisiblePredictions,
  hasHorizonMismatch,
  normalizeDomain,
  snapshotDisplayUpdatedAt,
  validateAddSourceRequest,
} from "../externalPredictionsUtils";
import type { ExternalPredictionRecord, ExternalPredictionSnapshot } from "@/lib/api";

describe("normalizeDomain", () => {
  it("strips scheme and www", () => {
    expect(normalizeDomain("https://www.Example.com/path")).toBe("example.com");
  });
});

describe("validateAddSourceRequest", () => {
  it("accepts valid domain and entry URL", () => {
    const result = validateAddSourceRequest({
      displayName: "My Broker",
      domains: ["example.com"],
      entryUrls: ["https://www.example.com/markets"],
    });
    expect(result.ok).toBe(true);
    expect(result.payload?.domains).toEqual(["example.com"]);
    expect(result.payload?.entry_urls).toEqual(["https://www.example.com/markets"]);
  });

  it("rejects mismatched entry host", () => {
    const result = validateAddSourceRequest({
      displayName: "My Broker",
      domains: ["example.com"],
      entryUrls: ["https://other.com/markets"],
    });
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/must match/i);
  });

  it("allows existing source id without entry URLs", () => {
    const result = validateAddSourceRequest({
      displayName: "My Broker",
      domains: ["example.com"],
      entryUrls: [],
      id: "my-broker",
    });
    expect(result.ok).toBe(true);
    expect(result.payload?.entry_urls).toEqual([]);
  });

  it("rejects URL without hostname", () => {
    const result = validateAddSourceRequest({
      displayName: "My Broker",
      domains: ["example.com"],
      entryUrls: ["https://"],
    });
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/invalid entry URL/i);
  });
});

describe("candidateNeedsEntryUrls", () => {
  it("returns true when entry_urls missing", () => {
    expect(candidateNeedsEntryUrls({ display_name: "X", domain: "x.com" })).toBe(true);
  });

  it("returns false when entry_urls present", () => {
    expect(
      candidateNeedsEntryUrls({
        display_name: "X",
        entry_urls: ["https://x.com/markets"],
      }),
    ).toBe(false);
  });
});

describe("buildAddSourcePayload", () => {
  it("normalizes candidate domains from domain field", () => {
    const result = buildAddSourcePayload(
      { display_name: "Foo", domain: "foo.com" },
      ["https://foo.com/markets"],
    );
    expect(result.ok).toBe(true);
    expect(result.payload?.domains).toEqual(["foo.com"]);
  });

  it("requires entry URLs in modal override path even with source id", () => {
    const result = buildAddSourcePayload({ display_name: "Foo", domain: "foo.com", id: "foo" }, []);
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/entry URL/i);
  });
});

describe("horizon helpers (the server's horizon_fit verdict is the only input)", () => {
  const rec = (horizon_fit?: boolean | null, extra: Partial<ExternalPredictionRecord> = {}): ExternalPredictionRecord =>
    ({ source_id: "t", horizon_days: 14, fetch_status: "ok", target: { mid: 25000 }, horizon_fit, ...extra }) as ExternalPredictionRecord;

  it("a stated date outside the window is a mismatch; fits, undated and absent are not (no badge for an unknown)", () => {
    expect(hasHorizonMismatch(rec(false))).toBe(true);
    expect(hasHorizonMismatch(rec(true))).toBe(false);
    expect(hasHorizonMismatch(rec(null))).toBe(false);
    expect(hasHorizonMismatch(rec(undefined))).toBe(false);
  });

  it("does not re-derive a verdict from the stored horizon_match: the server's word wins", () => {
    const stored = { horizon_match: { selected_days: 14, target_days_ahead: 345, in_window: false, soft_mismatch: true } };
    expect(hasHorizonMismatch(rec(true, { provenance: stored }))).toBe(false);
    expect(hasHorizonMismatch(rec(false, { provenance: { horizon_match: { in_window: true } } }))).toBe(true);
  });
});

describe("computeStreetSummary (D398: only a server-confirmed dated target counts)", () => {
  const ok = (source_id: string, mid: number, horizon_fit?: boolean | null): ExternalPredictionRecord =>
    ({ source_id, horizon_days: 14, as_of: "2026-10-12", spot_at_fetch: 25000, target: { mid }, fetch_status: "ok", horizon_fit }) as unknown as ExternalPredictionRecord;
  const snap = (predictions: ExternalPredictionRecord[], watchlisted = 13) =>
    ({ predictions, sources: Array.from({ length: watchlisted }, (_, i) => ({ id: `w${i}`, watchlisted: true })) }) as unknown as ExternalPredictionSnapshot;

  it("the real 14d snapshot shape: eight undated of nine ok, one dated in window, one year-out -> only the dated one counts", () => {
    const undatedMids = [23956.53, 23869.6, 23900, 23900, 23900, 26130.77, 23550, 25674.5];
    const s = computeStreetSummary(
      snap([...undatedMids.map((m, i) => ok(`u${i}`, m, null)), ok("univest", 24800, true), ok("implied", 28505.796, false)]), 14);
    expect([s.countedCount, s.targetMin, s.targetMax, s.targetMedian]).toEqual([1, 24800, 24800, 24800]);
    expect(s.undated.map((x) => x.sourceId)).toEqual(undatedMids.map((_, i) => `u${i}`));
    expect(s.longHorizon).toEqual([{ sourceId: "implied", mid: 28505.796 }]);
  });
  it("two dated matching sources give their range and median; undated and year-out ones stay out", () => {
    const s = computeStreetSummary(snap([ok("a", 23550, true), ok("b", 24100.4, true), ok("c", 28505.796, false), ok("d", 25000, null)]), 14);
    expect([s.countedCount, s.targetMin, s.targetMax]).toEqual([2, 23550, 24100.4]);
    expect(s.longHorizon).toEqual([{ sourceId: "c", mid: 28505.796 }]);
    expect(s.undated).toEqual([{ sourceId: "d", mid: 25000 }]);
  });
  it("a payload without the verdict (older server) is never assumed to fit", () => {
    const s = computeStreetSummary(snap([ok("a", 24000, undefined)]), 14);
    expect([s.countedCount, s.targetMedian]).toEqual([0, null]);
    expect(s.undated).toHaveLength(1);
  });
  it("all undated: no range at all; no forecasts or a null snapshot: nothing listed", () => {
    const none = computeStreetSummary(snap([ok("a", 24000, null), ok("b", 24100, null)]), 14);
    expect([none.targetMin, none.targetMax, none.targetMedian]).toEqual([null, null, null]);
    expect(none.undated).toHaveLength(2);
    const empty = computeStreetSummary(null, 14);
    expect([empty.countedCount, empty.longHorizon, empty.undated]).toEqual([0, [], []]);
  });
  it("a failed or mid-less record never counts nor is listed", () => {
    const bad = { ...ok("e", 1, true), fetch_status: "error" } as ExternalPredictionRecord;
    const noMid = { ...ok("m", 1, true), target: {} } as ExternalPredictionRecord;
    const s = computeStreetSummary(snap([bad, noMid]), 14);
    expect([s.countedCount, s.longHorizon, s.undated]).toEqual([0, [], []]);
  });
  it("rounds levels to whole points with Indian grouping, the page format", () => {
    expect(fmtLevel(28505.796)).toBe("28,506");
    expect(fmtLevel(2350000.4)).toBe("23,50,000");
    expect(fmtLevel(null)).toBe("—");
  });
});
