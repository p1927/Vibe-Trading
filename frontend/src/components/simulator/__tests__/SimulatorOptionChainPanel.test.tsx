import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SimulatorOptionChainPanel } from "../SimulatorOptionChainPanel";

const apiMock = vi.hoisted(() => ({
  getHubMarketDataOptionChain: vi.fn(),
  getHubMarketDataSpot: vi.fn(),
  getHubMarketDataOptionExpiries: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));

describe("SimulatorOptionChainPanel", () => {
  beforeEach(() => {
    apiMock.getHubMarketDataOptionChain.mockReset();
    apiMock.getHubMarketDataSpot.mockReset();
    apiMock.getHubMarketDataOptionExpiries.mockReset();
    apiMock.getHubMarketDataOptionExpiries.mockResolvedValue({
      status: "ok", underlying: "NIFTY", exchange: "NSE_INDEX", expiries: [],
    });
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not render anything when open=false", () => {
    apiMock.getHubMarketDataOptionChain.mockResolvedValue({
      status: "ok", underlying: "NIFTY", exchange: "NSE_INDEX", strikes: [],
    });
    apiMock.getHubMarketDataSpot.mockResolvedValue({
      status: "ok", symbol: "NIFTY", exchange: "NSE_INDEX", spot: null,
    });
    const { container } = render(
      <SimulatorOptionChainPanel symbol="NIFTY" open={false} onClose={() => {}} />,
    );
    expect(container.querySelector('[data-testid="option-chain-overlay"]')).toBeNull();
  });

  it("renders the option chain when open=true with strikes", async () => {
    apiMock.getHubMarketDataOptionChain.mockResolvedValue({
      status: "ok", underlying: "NIFTY", exchange: "NSE_INDEX",
      expiry_date: "2026-08-21", underlying_ltp: 24750.0,
      strikes: [
        { strike: 24700.0,
          ce: { last_price: 100.5, oi: 50000, iv: 14.5, delta: 0.5 },
          pe: { last_price: 80.0, oi: 30000, iv: 14.5, delta: -0.45 } },
        { strike: 24750.0,
          ce: { last_price: 70.0, oi: 40000, iv: 14.0, delta: 0.5 },
          pe: { last_price: 100.0, oi: 60000, iv: 14.0, delta: -0.5 } },
      ],
    });
    apiMock.getHubMarketDataSpot.mockResolvedValue({
      status: "ok", symbol: "NIFTY", exchange: "NSE_INDEX",
      spot: { symbol: "NIFTY", exchange: "NSE_INDEX", ltp: 24750.0, prev_close: 24700,
              source: "openalgo", as_of: "2026-08-15T09:31:00Z" },
    });

    render(<SimulatorOptionChainPanel symbol="NIFTY" open={true} onClose={() => {}} />);

    // The portal may render the panel anywhere in the document; waitFor polls
    // until the element appears. Use getAllByText since multiple cells match.
    await waitFor(() => {
      expect(screen.queryAllByText(/24,700/).length).toBeGreaterThan(0);
      expect(screen.queryAllByText(/24,750/).length).toBeGreaterThan(0);
    });
    expect(screen.getByText(/2026-08-21/)).toBeInTheDocument();
    expect(screen.getByText(/LTP 24,750/)).toBeInTheDocument();
  });

  it("shows the error message when the chain returns status=error", async () => {
    apiMock.getHubMarketDataOptionChain.mockResolvedValue({
      status: "error", underlying: "NIFTY", exchange: "NSE_INDEX",
      error: "OpenAlgo returned no chain",
    });
    apiMock.getHubMarketDataSpot.mockResolvedValue({
      status: "ok", symbol: "NIFTY", exchange: "NSE_INDEX", spot: null,
    });

    render(<SimulatorOptionChainPanel symbol="NIFTY" open={true} onClose={() => {}} />);

    await waitFor(() => {
      expect(screen.getByTestId("option-chain-error").textContent)
        .toMatch(/OpenAlgo returned no chain/);
    });
  });

  it("calls onClose when the X button is clicked", async () => {
    apiMock.getHubMarketDataOptionChain.mockResolvedValue({
      status: "ok", underlying: "NIFTY", exchange: "NSE_INDEX", strikes: [],
    });
    apiMock.getHubMarketDataSpot.mockResolvedValue({
      status: "ok", symbol: "NIFTY", exchange: "NSE_INDEX", spot: null,
    });
    const onClose = vi.fn();
    render(
      <SimulatorOptionChainPanel symbol="NIFTY" open={true} onClose={onClose} />,
    );
    await waitFor(() => {
      expect(screen.getByLabelText(/close option chain/i)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByLabelText(/close option chain/i));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("shows empty-state when no strikes are returned", async () => {
    apiMock.getHubMarketDataOptionChain.mockResolvedValue({
      status: "ok", underlying: "NIFTY", exchange: "NSE_INDEX", strikes: [],
    });
    apiMock.getHubMarketDataSpot.mockResolvedValue({
      status: "ok", symbol: "NIFTY", exchange: "NSE_INDEX", spot: null,
    });
    render(<SimulatorOptionChainPanel symbol="NIFTY" open={true} onClose={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText(/no strikes returned/i)).toBeInTheDocument();
    });
  });

  it("shows an expiry dropdown when multiple expiries are available and refetches on change", async () => {
    apiMock.getHubMarketDataOptionExpiries.mockResolvedValue({
      status: "ok", underlying: "NIFTY", exchange: "NSE_INDEX",
      expiries: ["2026-08-21", "2026-08-28"],
    });
    apiMock.getHubMarketDataOptionChain.mockResolvedValue({
      status: "ok", underlying: "NIFTY", exchange: "NSE_INDEX",
      expiry_date: "2026-08-21", underlying_ltp: 24750.0,
      strikes: [
        { strike: 24700.0, ce: { last_price: 100.5 }, pe: { last_price: 80.0 } },
      ],
    });
    apiMock.getHubMarketDataSpot.mockResolvedValue({
      status: "ok", symbol: "NIFTY", exchange: "NSE_INDEX",
      spot: { symbol: "NIFTY", exchange: "NSE_INDEX", ltp: 24750.0, prev_close: 24700,
              source: "openalgo", as_of: "2026-08-15T09:31:00Z" },
    });

    render(<SimulatorOptionChainPanel symbol="NIFTY" open={true} onClose={() => {}} />);

    const select = await screen.findByTestId("option-chain-expiry-select");
    await waitFor(() => {
      expect(apiMock.getHubMarketDataOptionChain).toHaveBeenCalledWith(
        expect.objectContaining({ expiry_date: "2026-08-21" }),
      );
    });

    fireEvent.change(select, { target: { value: "2026-08-28" } });

    await waitFor(() => {
      expect(apiMock.getHubMarketDataOptionChain).toHaveBeenCalledWith(
        expect.objectContaining({ expiry_date: "2026-08-28" }),
      );
    });
  });

  it("renders a moving line at the spot price once strikes and spot are loaded", async () => {
    apiMock.getHubMarketDataOptionChain.mockResolvedValue({
      status: "ok", underlying: "NIFTY", exchange: "NSE_INDEX",
      expiry_date: "2026-08-21", underlying_ltp: 24725.0,
      strikes: [
        { strike: 24700.0, ce: { last_price: 100.5 }, pe: { last_price: 80.0 } },
        { strike: 24750.0, ce: { last_price: 70.0 }, pe: { last_price: 100.0 } },
      ],
    });
    apiMock.getHubMarketDataSpot.mockResolvedValue({
      status: "ok", symbol: "NIFTY", exchange: "NSE_INDEX",
      spot: { symbol: "NIFTY", exchange: "NSE_INDEX", ltp: 24725.0, prev_close: 24700,
              source: "openalgo", as_of: "2026-08-15T09:31:00Z" },
    });

    render(<SimulatorOptionChainPanel symbol="NIFTY" open={true} onClose={() => {}} />);

    await waitFor(() => {
      expect(screen.getByTestId("option-chain-spot-line")).toBeInTheDocument();
    });
  });
});
