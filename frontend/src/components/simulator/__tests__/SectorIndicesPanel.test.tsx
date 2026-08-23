import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { SectorIndicesPanel } from "../SectorIndicesPanel";

const { apiMock, MockApiError } = vi.hoisted(() => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }
  return {
    apiMock: { getMarketSectorIndices: vi.fn(), getMarketIndexHistory: vi.fn(), getMarketTopConstituents: vi.fn() },
    MockApiError,
  };
});

vi.mock("@/lib/api", () => ({ api: apiMock, ApiError: MockApiError }));

describe("SectorIndicesPanel", () => {
  beforeEach(() => {
    Object.values(apiMock).forEach((fn) => fn.mockReset());
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders sector-index cards (filtering out headline entries) and a constituents table", async () => {
    apiMock.getMarketSectorIndices.mockResolvedValue({
      status: "ok",
      data: [
        { name: "SPX", label: "S&P 500 Index", kind: "headline" },
        { name: "SECTOR_TECHNOLOGY", label: "Technology Sector (XLK)", kind: "sector" },
      ],
    });
    apiMock.getMarketIndexHistory.mockResolvedValue({
      status: "ok",
      data: [
        { date: "2024-05-01", close: 100 },
        { date: "2024-05-02", close: 105 },
      ],
    });
    apiMock.getMarketTopConstituents.mockResolvedValue({
      status: "ok",
      data: [{ symbol: "NSE:RELIANCE", name: "RELIANCE", close: 1316, market_cap_basic: 1.8e11, sector: "Energy" }],
    });

    render(<SectorIndicesPanel country="US" label="US" />);

    // Headline entry (SPX) should NOT appear as a card here — GlobalMarketsPanel already shows it.
    await waitFor(() => expect(screen.getByText("Technology Sector (XLK)")).toBeInTheDocument());
    expect(screen.queryByText("S&P 500 Index")).not.toBeInTheDocument();

    await waitFor(() => expect(screen.getByText("NSE:RELIANCE")).toBeInTheDocument());
    expect(screen.getByText("Energy")).toBeInTheDocument();
  });

  it("renders an honest not-sourced note for constituents instead of crashing", async () => {
    apiMock.getMarketSectorIndices.mockResolvedValue({ status: "ok", data: [] });
    apiMock.getMarketTopConstituents.mockRejectedValue(new MockApiError("no constituent query", 404));

    render(<SectorIndicesPanel country="US" label="US" />);

    await waitFor(() => expect(screen.getByTestId("constituents-not-sourced")).toBeInTheDocument());
    expect(screen.getByText(/No sector indices wired/i)).toBeInTheDocument();
  });

  it("surfaces a real constituents fetch error distinctly from a not-sourced gap", async () => {
    apiMock.getMarketSectorIndices.mockResolvedValue({ status: "ok", data: [] });
    apiMock.getMarketTopConstituents.mockRejectedValue(new Error("network error"));

    render(<SectorIndicesPanel country="US" label="US" />);

    await waitFor(() => expect(screen.getByText("network error")).toBeInTheDocument());
    expect(screen.queryByTestId("constituents-not-sourced")).not.toBeInTheDocument();
  });

  it("refetches both sections when the country prop changes", async () => {
    apiMock.getMarketSectorIndices.mockResolvedValue({ status: "ok", data: [] });
    apiMock.getMarketTopConstituents.mockResolvedValue({ status: "ok", data: [] });

    const { rerender } = render(<SectorIndicesPanel country="US" label="US" />);
    await waitFor(() => expect(apiMock.getMarketSectorIndices).toHaveBeenCalledWith("US"));
    await waitFor(() => expect(apiMock.getMarketTopConstituents).toHaveBeenCalledWith("US", 10));

    rerender(<SectorIndicesPanel country="IN" label="India" />);
    await waitFor(() => expect(apiMock.getMarketSectorIndices).toHaveBeenCalledWith("IN"));
    await waitFor(() => expect(apiMock.getMarketTopConstituents).toHaveBeenCalledWith("IN", 10));
  });
});
