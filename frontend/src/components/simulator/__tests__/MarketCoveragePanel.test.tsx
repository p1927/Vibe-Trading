import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MarketCoveragePanel } from "../MarketCoveragePanel";

const apiMock = vi.hoisted(() => ({
  getMarketReplayCalendar: vi.fn(),
  backfillMarketTicks: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));

const CALENDAR = {
  status: "ok",
  days: [{ date: "2024-05-01", has_spx: true, spx_rows: 1 }],
  indices: ["SPX", "NASDAQ", "DOW"],
};

const TWO_INDEX_CALENDAR = {
  status: "ok",
  days: [{ date: "2024-05-01", nikkei225_rows: 1 }],
  indices: ["NIKKEI225", "TOPIX"],
};

const FULL_COVERAGE_CALENDAR = {
  status: "ok",
  days: [{ date: "2024-05-01", spx_rows: 1, nasdaq_rows: 1, dow_rows: 1 }],
  indices: ["SPX", "NASDAQ", "DOW"],
};

describe("MarketCoveragePanel", () => {
  beforeEach(() => {
    Object.values(apiMock).forEach((fn) => fn.mockReset());
    apiMock.getMarketReplayCalendar.mockResolvedValue(CALENDAR);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("reports how many of the last 120 days are missing", async () => {
    render(<MarketCoveragePanel country="US" />);

    await waitFor(() => expect(screen.getByText(/119 days missing/i)).toBeInTheDocument());
  });

  it("clicking a missing day backfills, and a recorded day is not clickable", async () => {
    apiMock.backfillMarketTicks.mockResolvedValue({ status: "ok", results: [] });

    render(<MarketCoveragePanel country="US" />);

    const recordedDay = await screen.findByTestId("market-coverage-day-2024-05-01");
    expect(recordedDay).toBeDisabled();

    const missingDay = await screen.findByTestId("market-coverage-day-2024-04-30");
    fireEvent.click(missingDay);

    await waitFor(() => expect(apiMock.backfillMarketTicks).toHaveBeenCalledWith("US"));
  });

  it("shows a per-index legend and marks a partially-covered day distinctly from a fully-covered one for 3+ index markets", async () => {
    apiMock.getMarketReplayCalendar.mockResolvedValue(CALENDAR);

    render(<MarketCoveragePanel country="US" />);

    // Legend: one swatch per index (SPX/NASDAQ/DOW), only rendered for 3+ index markets.
    await waitFor(() => expect(screen.getByText("SPX")).toBeInTheDocument());
    expect(screen.getByText("NASDAQ")).toBeInTheDocument();
    expect(screen.getByText("DOW")).toBeInTheDocument();

    // Only SPX has rows this day -- the cell must read as partial, not "fully covered".
    const partialDay = await screen.findByTestId("market-coverage-day-2024-05-01");
    expect(partialDay.title).toBe("2024-05-01 · SPX 1 · NASDAQ 0 · DOW 0");
    expect(partialDay.className).toContain("bg-emerald-500/25");
  });

  it("renders a fully-covered day differently from a partially-covered one", async () => {
    apiMock.getMarketReplayCalendar.mockResolvedValue(FULL_COVERAGE_CALENDAR);

    render(<MarketCoveragePanel country="US" />);

    const fullDay = await screen.findByTestId("market-coverage-day-2024-05-01");
    expect(fullDay.className).toContain("bg-emerald-500/50");
    expect(fullDay.className).not.toContain("bg-emerald-500/25");
  });

  it("does not render per-index stripes/legend for a market with fewer than 3 indices", async () => {
    apiMock.getMarketReplayCalendar.mockResolvedValue(TWO_INDEX_CALENDAR);

    render(<MarketCoveragePanel country="JP" />);

    const day = await screen.findByTestId("market-coverage-day-2024-05-01");
    expect(day.title).toBe("2024-05-01 · 1 row");
    expect(screen.queryByText("NIKKEI225")).not.toBeInTheDocument();
  });
});
