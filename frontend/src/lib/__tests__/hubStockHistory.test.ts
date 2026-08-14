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

  it("getHubMacroFactorPanel sends start and end as required", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockJsonResponse({ status: "ok", start: "2026-08-01", end: "2026-08-31",
        rows: [], columns: [] }),
    );
    await api.getHubMacroFactorPanel({ start: "2026-08-01", end: "2026-08-31" });
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("/trade/hub/macro-factors/panel");
    expect(url).toContain("start=2026-08-01");
    expect(url).toContain("end=2026-08-31");
  });

  it("getHubMacroFactorLatest and getHubMacroFactorDates use correct paths", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockJsonResponse({ status: "ok", day: "2026-08-15", factors: { us_10y: 4.2 } }),
    );
    await api.getHubMacroFactorLatest();
    expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0])
      .toContain("/trade/hub/macro-factors/latest");

    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockJsonResponse({ status: "ok", dates: ["2026-08-01"] }),
    );
    await api.getHubMacroFactorDates();
    expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[1][0])
      .toContain("/trade/hub/macro-factors/dates");
  });

  it("getHubIndexHistoryDays and getHubIndexHistoryExpiries encode symbol/exchange", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockJsonResponse({ status: "ok", symbol: "NIFTY", exchange: "NSE_INDEX", days: [] }),
    );
    await api.getHubIndexHistoryDays({ symbol: "NIFTY" });
    let url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("/trade/hub/index-history/days");

    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockJsonResponse({ status: "ok", symbol: "NIFTY", exchange: "NSE_INDEX", expiries: [] }),
    );
    await api.getHubIndexHistoryExpiries({ symbol: "NIFTY" });
    url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[1][0] as string;
    expect(url).toContain("/trade/hub/index-history/expiries");
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

  it("getHubConstituentsPanel passes start/end/limit through query string", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockJsonResponse({ status: "ok", rows: [] }),
    );
    await api.getHubConstituentsPanel({ start: "2025-01-01", end: "2025-12-31", limit: 100 });
    const url = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
    expect(url).toContain("/trade/hub/constituents/panel");
    expect(url).toContain("start=2025-01-01");
    expect(url).toContain("end=2025-12-31");
    expect(url).toContain("limit=100");
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
