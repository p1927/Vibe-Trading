import {
  buildAddSourcePayload,
  candidateNeedsEntryUrls,
  effectiveChartHorizonDays,
  hasHorizonMismatch,
  normalizeDomain,
  validateAddSourceRequest,
} from "../externalPredictionsUtils";
import type { ExternalPredictionRecord } from "@/lib/api";

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
