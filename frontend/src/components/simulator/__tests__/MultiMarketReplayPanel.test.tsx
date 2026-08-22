import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MultiMarketReplayPanel } from "../MultiMarketReplayPanel";

const apiMock = vi.hoisted(() => ({
  getMarketRegistry: vi.fn(),
  getMultiMarketStatus: vi.fn(),
  armMultiMarketReplay: vi.fn(),
  pauseMultiMarketReplay: vi.fn(),
  resumeMultiMarketReplay: vi.fn(),
  setMultiMarketReplaySpeed: vi.fn(),
  stopMultiMarketReplay: vi.fn(),
  getMultiMarketQuote: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));

const STATUS = {
  status: "ok",
  markets: ["IN", "US"],
  clock: { start_utc: "2026-08-23T00:00:00+00:00", sim_now_utc: "2026-08-23T00:00:00+00:00", speed: 1, paused: false },
  market_status: {
    IN: { market: "IN", session_open: false, local_time: "2026-08-23T05:30:00+05:30", timezone: "Asia/Kolkata" },
    US: { market: "US", session_open: false, local_time: "2026-08-22T20:00:00-04:00", timezone: "America/New_York" },
  },
};

describe("MultiMarketReplayPanel", () => {
  beforeEach(() => {
    Object.values(apiMock).forEach((fn) => fn.mockReset());
    apiMock.getMarketRegistry.mockResolvedValue({
      status: "ok",
      markets: [
        { code: "IN", currency: "INR", timezone: "Asia/Kolkata", indices: ["NIFTY50"] },
        { code: "US", currency: "USD", timezone: "America/New_York", indices: ["SPX", "NASDAQ", "DOW"] },
      ],
    });
    apiMock.getMultiMarketStatus.mockRejectedValue(new Error("no session armed"));
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders market checkboxes and arms a session on click", async () => {
    apiMock.armMultiMarketReplay.mockResolvedValue(STATUS);

    render(<MultiMarketReplayPanel />);

    await waitFor(() => expect(screen.getByText(/arm 2 markets/i)).toBeInTheDocument());

    fireEvent.click(screen.getByText(/arm 2 markets/i));

    await waitFor(() => expect(apiMock.armMultiMarketReplay).toHaveBeenCalledWith({ markets: ["IN", "US"], speed: 1 }));
    await waitFor(() => expect(screen.getByText("Pause")).toBeInTheDocument());
    // "India"/"US" appear twice each (status table row + quote-lookup <select> option).
    expect(screen.getAllByText("India").length).toBeGreaterThan(0);
    expect(screen.getAllByText("US").length).toBeGreaterThan(0);
  });

  it("surfaces an arm error instead of silently doing nothing", async () => {
    apiMock.armMultiMarketReplay.mockRejectedValue(new Error("unsupported market(s) ['XX']"));

    render(<MultiMarketReplayPanel />);
    await waitFor(() => expect(screen.getByText(/arm 2 markets/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/arm 2 markets/i));

    await waitFor(() => expect(screen.getByText(/unsupported market/i)).toBeInTheDocument());
  });

  it("stops a session and returns to the arm screen", async () => {
    apiMock.armMultiMarketReplay.mockResolvedValue(STATUS);
    apiMock.stopMultiMarketReplay.mockResolvedValue({ status: "ok", message: "stopped" });

    render(<MultiMarketReplayPanel />);
    await waitFor(() => expect(screen.getByText(/arm 2 markets/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/arm 2 markets/i));
    await waitFor(() => expect(screen.getByText("Stop")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Stop"));

    await waitFor(() => expect(screen.getByText(/arm 2 markets/i)).toBeInTheDocument());
  });
});
