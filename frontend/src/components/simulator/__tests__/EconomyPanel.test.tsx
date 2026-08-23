import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { EconomyPanel } from "../EconomyPanel";

const { apiMock, MockApiError } = vi.hoisted(() => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }
  return { apiMock: { getMarketEconomyFactor: vi.fn() }, MockApiError };
});

vi.mock("@/lib/api", () => ({ api: apiMock, ApiError: MockApiError }));

const MARKETS = ["IN", "US", "CN", "JP", "RU", "ME", "LATAM"];

describe("EconomyPanel", () => {
  beforeEach(() => {
    apiMock.getMarketEconomyFactor.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders the cross-market chart data for the default factor", async () => {
    apiMock.getMarketEconomyFactor.mockImplementation((country: string) => {
      if (country === "IN") {
        return Promise.resolve({
          status: "ok",
          data: [
            { year: "2023", value: 7.2 },
            { year: "2024", value: 6.8 },
          ],
        });
      }
      if (country === "US") {
        return Promise.resolve({
          status: "ok",
          data: [{ year: "2024", value: 2.5 }],
        });
      }
      return Promise.reject(new MockApiError("No catalog entry", 404));
    });

    render(<EconomyPanel />);

    await waitFor(() => expect(apiMock.getMarketEconomyFactor).toHaveBeenCalledTimes(MARKETS.length));
    // The per-market status grid renders each market's latest value as plain text (recharts
    // doesn't lay out SVG in jsdom, so assert against that rather than the chart itself).
    await waitFor(() => expect(screen.getByText("6.8")).toBeInTheDocument());
    expect(screen.getByText("2.5")).toBeInTheDocument();
    // The 5 markets with no mocked data resolve to a 404 -> honest "not sourced" state.
    expect(screen.getAllByText("Not sourced for this market").length).toBe(MARKETS.length - 2);
  });

  it("renders a not-sourced state gracefully instead of crashing", async () => {
    apiMock.getMarketEconomyFactor.mockRejectedValue(new MockApiError("No catalog entry", 404));

    render(<EconomyPanel />);

    await waitFor(() =>
      expect(screen.getAllByText("Not sourced for this market").length).toBe(MARKETS.length),
    );
    expect(screen.getByText(/No market has sourced data/i)).toBeInTheDocument();
  });

  it("surfaces a real fetch error distinctly from a not-sourced gap", async () => {
    apiMock.getMarketEconomyFactor.mockImplementation((country: string) =>
      country === "IN"
        ? Promise.reject(new Error("network error"))
        : Promise.reject(new MockApiError("No catalog entry", 404)),
    );

    render(<EconomyPanel />);

    await waitFor(() => expect(screen.getByText("network error")).toBeInTheDocument());
  });

  it("switches factors and refetches for every market", async () => {
    apiMock.getMarketEconomyFactor.mockResolvedValue({ status: "ok", data: [{ year: "2024", value: 1 }] });

    render(<EconomyPanel />);
    await waitFor(() => expect(apiMock.getMarketEconomyFactor).toHaveBeenCalledTimes(MARKETS.length));
    expect(apiMock.getMarketEconomyFactor).toHaveBeenCalledWith("IN", "gdp_growth");

    apiMock.getMarketEconomyFactor.mockClear();
    fireEvent.change(screen.getByTestId("economy-factor-picker"), { target: { value: "unemployment_rate" } });

    await waitFor(() => expect(apiMock.getMarketEconomyFactor).toHaveBeenCalledTimes(MARKETS.length));
    expect(apiMock.getMarketEconomyFactor).toHaveBeenCalledWith("IN", "unemployment_rate");
  });

  it("switches to per-market view and fetches all 6 factors for the selected market", async () => {
    const FACTOR_COUNT = 6;
    apiMock.getMarketEconomyFactor.mockImplementation((_country: string, series: string) => {
      if (series === "industrial_production") return Promise.reject(new MockApiError("No catalog entry", 404));
      return Promise.resolve({
        status: "ok",
        data: [
          { year: "2023", value: 1 },
          { year: "2024", value: 2 },
        ],
      });
    });

    render(<EconomyPanel />);
    await waitFor(() => expect(apiMock.getMarketEconomyFactor).toHaveBeenCalledTimes(MARKETS.length));

    apiMock.getMarketEconomyFactor.mockClear();
    fireEvent.click(screen.getByText("Per-market"));

    await waitFor(() => expect(apiMock.getMarketEconomyFactor).toHaveBeenCalledTimes(FACTOR_COUNT));
    expect(apiMock.getMarketEconomyFactor).toHaveBeenCalledWith("IN", "gdp_growth");
    expect(apiMock.getMarketEconomyFactor).toHaveBeenCalledWith("IN", "industrial_production");
    await waitFor(() =>
      expect(screen.getByTestId("economy-detail-not-sourced-industrial_production")).toBeInTheDocument(),
    );

    apiMock.getMarketEconomyFactor.mockClear();
    fireEvent.change(screen.getByTestId("economy-market-picker"), { target: { value: "US" } });

    await waitFor(() => expect(apiMock.getMarketEconomyFactor).toHaveBeenCalledTimes(FACTOR_COUNT));
    expect(apiMock.getMarketEconomyFactor).toHaveBeenCalledWith("US", "gdp_growth");
  });
});
