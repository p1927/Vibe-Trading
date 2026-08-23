import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MarketReplayPanel } from "../MarketReplayPanel";

const apiMock = vi.hoisted(() => ({
  getMarketReplayCalendar: vi.fn(),
  backfillMarketTicks: vi.fn(),
  getMultiMarketStatus: vi.fn(),
  armMultiMarketReplay: vi.fn(),
  pauseMultiMarketReplay: vi.fn(),
  resumeMultiMarketReplay: vi.fn(),
  setMultiMarketReplaySpeed: vi.fn(),
  stopMultiMarketReplay: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));

const CALENDAR = {
  status: "ok",
  days: [
    { date: "2024-05-01", has_spx: true, spx_rows: 1 },
    { date: "2024-04-29", has_spx: true, spx_rows: 1 },
  ],
  indices: ["SPX", "NASDAQ", "DOW"],
};

const STATUS = {
  status: "ok",
  markets: ["US"],
  clock: {
    start_utc: "2024-05-01T00:00:00Z",
    sim_now_utc: "2024-05-01T00:00:00Z",
    speed: 1,
    paused: false,
    end_utc: null,
    loop: false,
    completed: false,
  },
  market_status: {
    US: { market: "US", session_open: false, local_time: "2024-04-30T20:00:00-04:00", timezone: "America/New_York" },
  },
};

describe("MarketReplayPanel", () => {
  beforeEach(() => {
    Object.values(apiMock).forEach((fn) => fn.mockReset());
    apiMock.getMarketReplayCalendar.mockResolvedValue(CALENDAR);
    apiMock.getMultiMarketStatus.mockRejectedValue(new Error("no session armed"));
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("clicking a missing day triggers a backfill", async () => {
    apiMock.backfillMarketTicks.mockResolvedValue({ status: "ok", results: [] });

    render(<MarketReplayPanel country="US" label="US" />);

    await waitFor(() => expect(apiMock.getMarketReplayCalendar).toHaveBeenCalledWith("US"));
    // "2024-04-30" is inside the 120-day window ending 2024-05-01 (the only recorded day) but
    // isn't in `CALENDAR.days` itself, so it's a deterministic "missing" cell.
    const missingDay = await screen.findByTestId("market-replay-day-2024-04-30");
    fireEvent.click(missingDay);

    await waitFor(() => expect(apiMock.backfillMarketTicks).toHaveBeenCalledWith("US"));
  });

  it("selecting the recorded day and arming calls armMultiMarketReplay scoped to the country", async () => {
    apiMock.armMultiMarketReplay.mockResolvedValue(STATUS);

    render(<MarketReplayPanel country="US" label="US" />);

    const recordedDay = await screen.findByTestId("market-replay-day-2024-05-01");
    fireEvent.click(recordedDay);

    await waitFor(() => expect(screen.getByRole("button", { name: /arm replay/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /arm replay/i }));

    await waitFor(() =>
      expect(apiMock.armMultiMarketReplay).toHaveBeenCalledWith({
        markets: ["US"],
        start_utc: "2024-05-01T00:00:00Z",
        end_utc: undefined,
        speed: 1,
        loop: false,
      }),
    );
    await waitFor(() => expect(screen.getByText("Stop")).toBeInTheDocument());
  });

  it("selecting two days arms a date range with the loop flag", async () => {
    apiMock.armMultiMarketReplay.mockResolvedValue(STATUS);

    render(<MarketReplayPanel country="US" label="US" />);

    fireEvent.click(await screen.findByTestId("market-replay-day-2024-04-29"));
    fireEvent.click(await screen.findByTestId("market-replay-day-2024-05-01"));

    await waitFor(() => expect(screen.getByTestId("market-replay-loop")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("market-replay-loop"));
    fireEvent.click(screen.getByRole("button", { name: /arm replay/i }));

    await waitFor(() =>
      expect(apiMock.armMultiMarketReplay).toHaveBeenCalledWith({
        markets: ["US"],
        start_utc: "2024-04-29T00:00:00Z",
        end_utc: "2024-05-01T23:59:59Z",
        speed: 1,
        loop: true,
      }),
    );
  });
});
