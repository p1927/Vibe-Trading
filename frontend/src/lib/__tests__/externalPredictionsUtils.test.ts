import {
  buildAddSourcePayload,
  candidateNeedsEntryUrls,
  computeStreetSummary,
  fmtLevel,
  effectiveChartHorizonDays,
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

describe("horizon helpers", () => {
  const baseRecord: ExternalPredictionRecord = {
    source_id: "test",
    horizon_days: 14,
    as_of: "2026-07-20",
    spot_at_fetch: 24000,
    target: { mid: 25000 },
    target_date: "2026-08-03",
    fetch_status: "ok",
    provenance: {
      horizon_match: {
        selected_days: 14,
        target_days_ahead: 14,
        in_window: true,
      },
    },
  };

  it("detects horizon mismatch", () => {
    const mismatched: ExternalPredictionRecord = {
      ...baseRecord,
      target_date: "2027-06-30",
      provenance: {
        horizon_match: {
          selected_days: 14,
          target_days_ahead: 345,
          in_window: false,
          soft_mismatch: true,
        },
      },
    };
    expect(hasHorizonMismatch(mismatched)).toBe(true);
    expect(effectiveChartHorizonDays(mismatched, 14)).toBeGreaterThan(14);
  });

  it("uses tab horizon when article horizon matches", () => {
    expect(hasHorizonMismatch(baseRecord)).toBe(false);
    expect(effectiveChartHorizonDays(baseRecord, 14)).toBe(14);
  });
});

describe("filterIssuePredictions", () => {
  it("includes error and not_found but excludes ok forecasts", () => {
    const rows = filterIssuePredictions([
      { source_id: "a", fetch_status: "ok", target: { mid: 25000 } },
      { source_id: "b", fetch_status: "not_found", error_message: "listing_page_not_forecast" },
      { source_id: "c", fetch_status: "error", error_message: "timeout" },
      { source_id: "d", fetch_status: "ok", target: {} },
    ]);
    expect(rows.map((r) => r.source_id)).toEqual(["b", "c"]);
  });
});

describe("snapshotDisplayUpdatedAt", () => {
  it("prefers refresh_completed_at over fetched_at", () => {
    expect(
      snapshotDisplayUpdatedAt({
        fetched_at: "2026-07-23T10:00:00+00:00",
        refresh_completed_at: "2026-07-23T10:45:00+00:00",
      }),
    ).toBe("2026-07-23T10:45:00+00:00");
  });

  it("falls back to fetched_at when completion missing", () => {
    expect(snapshotDisplayUpdatedAt({ fetched_at: "2026-07-23T10:00:00+00:00" })).toBe(
      "2026-07-23T10:00:00+00:00",
    );
  });
});

describe("computeStreetSummary", () => {
  const ok = (source_id: string, mid: number, horizon_match?: Record<string, unknown>): ExternalPredictionRecord =>
    ({ source_id, horizon_days: 14, as_of: "2026-10-12", spot_at_fetch: 25000, target: { mid }, fetch_status: "ok",
      provenance: horizon_match ? { horizon_match } : {} }) as unknown as ExternalPredictionRecord;
  const snap = (predictions: ExternalPredictionRecord[]) => ({ predictions, sources: [] }) as unknown as ExternalPredictionSnapshot;
  const yearOut = { selected_days: 14, target_days_ahead: 261, in_window: false, soft_mismatch: true };

  it("keeps a year-out target out of the range, median and count of the range, and lists it apart (QA C9 numbers)", () => {
    const s = computeStreetSummary(snap([ok("a", 23550), ok("b", 24100.4), ok("c", 28505.796, yearOut)]), 14);
    expect([s.targetMin, s.targetMax, s.targetMedian]).toEqual([23550, 24100.4, 24100.4]);
    expect(s.longHorizon).toEqual([{ sourceId: "c", mid: 28505.796 }]);
    expect(s.forecastCount).toBe(3);
  });
  it("rounds levels to whole points with Indian grouping, the page format", () => {
    expect(fmtLevel(28505.796)).toBe("28,506");
    expect(fmtLevel(2350000.4)).toBe("23,50,000");
    expect(fmtLevel(null)).toBe("—");
  });
  it("only mismatched targets: no range, all listed; none mismatched: nothing listed; an in-window flag is not a mismatch", () => {
    const only = computeStreetSummary(snap([ok("c", 28505, yearOut)]), 14);
    expect([only.targetMin, only.targetMax, only.targetMedian]).toEqual([null, null, null]);
    expect(only.longHorizon).toHaveLength(1);
    const fine = computeStreetSummary(snap([ok("a", 24000, { selected_days: 14, target_days_ahead: 14, in_window: true })]), 14);
    expect(fine.longHorizon).toEqual([]);
    expect(fine.targetMedian).toBe(24000);
    expect(computeStreetSummary(null, 14).longHorizon).toEqual([]);
  });
});
