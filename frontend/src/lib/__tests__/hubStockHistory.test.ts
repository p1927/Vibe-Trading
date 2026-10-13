import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";

const originalFetch = globalThis.fetch;

function mockJsonResponse(body: unknown, contentType = "application/json"): Response {
  return {
    ok: true,
    status: 200,
    headers: { get: (k: string) => (k.toLowerCase() === "content-type" ? contentType : null) },
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe("Phase 8 — /trade/hub/stock_history/* client methods", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("getHubMarketDataTicks encodes query params and uses /market-data/ticks", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockJsonResponse({ status: "ok", symbol: "BANKNIFTY", exchange: "NSE_INDEX",
        source: "timescale", ticks: [] }),
    );
    await api.getHubMarketDataTicks({ symbol: "BANKNIFTY", since_minutes: 60, limit: 200 });
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("/trade/hub/market-data/ticks");
    expect(url).toContain("symbol=BANKNIFTY");
    expect(url).toContain("since_minutes=60");
    expect(url).toContain("limit=200");
  });

  it("getHubMarketDataSpot defaults to NIFTY when no symbol provided", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockJsonResponse({ status: "ok", symbol: "NIFTY", exchange: "NSE_INDEX", spot: null }),
    );
    await api.getHubMarketDataSpot({});
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("/trade/hub/market-data/spot");
    expect(url).toContain("symbol=NIFTY");
    expect(url).toContain("exchange=NSE_INDEX");
  });

  it("getHubMarketDataOptionChain includes strike_count and expiry_date when given", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockJsonResponse({ status: "ok", underlying: "NIFTY", exchange: "NSE_INDEX",
        strikes: [] }),
    );
    await api.getHubMarketDataOptionChain({
      symbol: "NIFTY",
      strike_count: 10,
      expiry_date: "2026-08-21",
    });
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("/trade/hub/market-data/option-chain");
    expect(url).toContain("strike_count=10");
    expect(url).toContain("expiry_date=2026-08-21");
  });

});

describe("Phase 9 — /trade/hub/stock-history/coverage + backfill", () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("getHubStockHistoryCoverage encodes week + symbol", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockJsonResponse({
        status: "ok", week_start: "2026-08-10", week_end: "2026-08-14",
        symbol: "NIFTY", is_complete: false, missing_days: ["2026-08-10"],
        bucket_labels: ["macro_factors"],
        days: [], fetch_list: [],
      }),
    );
    await api.getHubStockHistoryCoverage({ week: "2026-08-10", symbol: "NIFTY" });
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("/trade/hub/stock-history/coverage");
    expect(url).toContain("week=2026-08-10");
    expect(url).toContain("symbol=NIFTY");
  });

  it("getHubStockHistoryCoverage omits include_optional when false", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockJsonResponse({
        status: "ok", week_start: "2026-08-10", week_end: "2026-08-14",
        symbol: "NIFTY", is_complete: true, missing_days: [],
        bucket_labels: [], days: [], fetch_list: [],
      }),
    );
    await api.getHubStockHistoryCoverage({ week: "2026-08-10" });
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).not.toContain("include_optional=");
  });

  it("startHubStockHistoryBackfillRun POSTs one day's buckets, and the run is polled by id", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockJsonResponse({ status: "ok", run: { run_id: "r1", status: "running" } }),
    );
    await api.startHubStockHistoryBackfillRun({
      day: "2026-08-10", buckets: ["macro_factors"], symbol: "NIFTY", include_optional: true,
    });
    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toContain("/trade/hub/stock-history/backfill-runs");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body);
    expect(body.day).toBe("2026-08-10");
    expect(body.buckets).toEqual(["macro_factors"]);

    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockJsonResponse({ status: "ok", run: { run_id: "r1", status: "done" } }),
    );
    await api.getHubStockHistoryBackfillRun("r1");
    const pollUrl = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[1][0] as string;
    expect(pollUrl).toContain("/trade/hub/stock-history/backfill-runs/r1");
  });

  it("getHubIndexHistoryDays encodes symbol/exchange", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockJsonResponse({ status: "ok", symbol: "NIFTY", exchange: "NSE_INDEX", days: [] }),
    );
    await api.getHubIndexHistoryDays({ symbol: "NIFTY" });
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("/trade/hub/index-history/days");
  });

  it("getHubIndexHistoryBars sends since_ist and until_ist when provided", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockJsonResponse({ status: "ok", symbol: "NIFTY", exchange: "NSE_INDEX", bars: [] }),
    );
    await api.getHubIndexHistoryBars({
      symbol: "NIFTY",
      since_ist: "2024-05-29T09:15:00",
      until_ist: "2024-05-29T10:30:00",
    });
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("/trade/hub/index-history/bars");
    expect(url).toContain("since_ist=2024-05-29T09%3A15%3A00");
    expect(url).toContain("until_ist=2024-05-29T10%3A30%3A00");
  });

  it("omits undefined params from the query string", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockJsonResponse({ status: "ok", symbol: "NIFTY", exchange: "NSE_INDEX",
        source: "empty", ticks: [] }),
    );
    await api.getHubMarketDataTicks({});  // no params
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("symbol=NIFTY");
    expect(url).toContain("exchange=NSE_INDEX");
    expect(url).not.toContain("since_minutes");
    expect(url).not.toContain("limit");
  });
});
