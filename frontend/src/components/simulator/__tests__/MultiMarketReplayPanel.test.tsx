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
  listSchedulerRegistry: vi.fn(),
  pauseStockSimSchedulerEntry: vi.fn(),
  resumeStockSimSchedulerEntry: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
}));

function recordingEntry(overrides: Record<string, unknown> = {}) {
  return {
    id: "B:recorder:us:index",
    source: "stock_simulator",
    section: "recorder:us",
    label: "us/index",
    description: null,
    schedule_kind: "recorder_interval",
    schedule_display: "every 300s (estimated)",
    enabled: true,
    status: "idle",
    cancel_requested: false,
    next_run_at: null,
    last_run_at: null,
    last_error: null,
    auto_paused_reason: null,
    supports_live_log: true,
    live_log_stream_url: null,
    persisted_default_enabled: null,
    controls: { pause: true, resume: false, cancel: false, delete: false, trigger_now: true },
    ...overrides,
  };
}

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
    apiMock.listSchedulerRegistry.mockResolvedValue({ status: "ok", entries: [], sources: {} });
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

  it("labels an interpolated quote as simulated, distinct from a stale one", async () => {
    apiMock.armMultiMarketReplay.mockResolvedValue(STATUS);
    apiMock.getMultiMarketQuote.mockResolvedValue({
      status: "ok",
      data: {
        market: "US", symbol: "SPX", exchange: "US_INDEX", price: 150,
        ts: "2026-08-24T15:00:00+00:00", stale: false, synthetic: true, source: "interpolated_ohlc",
      },
    });

    render(<MultiMarketReplayPanel />);
    await waitFor(() => expect(screen.getByText(/arm 2 markets/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/arm 2 markets/i));
    await waitFor(() => expect(screen.getByText("Pause")).toBeInTheDocument());

    const [marketSelect, symbolSelect] = screen.getAllByRole("combobox");
    fireEvent.change(marketSelect, { target: { value: "US" } });
    await waitFor(() => expect(symbolSelect).not.toBeDisabled());
    fireEvent.change(symbolSelect, { target: { value: "SPX" } });
    fireEvent.click(screen.getByText("Get quote"));

    await waitFor(() => expect(screen.getByText(/simulated.*interpolated open→close/i)).toBeInTheDocument());
    expect(screen.queryByText(/stale — held over/i)).not.toBeInTheDocument();
  });

  describe("Recording sub-section", () => {
    it("groups recorder categories by market and shows the active count", async () => {
      apiMock.listSchedulerRegistry.mockResolvedValue({
        status: "ok",
        entries: [
          recordingEntry({ id: "B:recorder:us:index", section: "recorder:us", enabled: true }),
          recordingEntry({
            id: "B:recorder:us:policy",
            section: "recorder:us",
            enabled: false,
            controls: { pause: false, resume: true, cancel: false, delete: false, trigger_now: false },
            persisted_default_enabled: false,
          }),
          recordingEntry({ id: "B:recorder:in_economy:economy", section: "recorder:in_economy", enabled: true }),
        ],
        sources: {},
      });

      render(<MultiMarketReplayPanel />);

      await waitFor(() => expect(screen.getByText("2/3 active")).toBeInTheDocument());
      expect(screen.getByText("US")).toBeInTheDocument();
      expect(screen.getByText("India — Economy")).toBeInTheDocument();
      expect(screen.getByText("default: off")).toBeInTheDocument();
    });

    it("pauses an active category on click and refreshes its state", async () => {
      apiMock.listSchedulerRegistry
        .mockResolvedValueOnce({
          status: "ok",
          entries: [recordingEntry({ enabled: true, persisted_default_enabled: null })],
          sources: {},
        })
        .mockResolvedValue({
          status: "ok",
          entries: [
            recordingEntry({
              enabled: false,
              persisted_default_enabled: false,
              controls: { pause: false, resume: true, cancel: false, delete: false, trigger_now: false },
            }),
          ],
          sources: {},
        });
      apiMock.pauseStockSimSchedulerEntry.mockResolvedValue({ status: "ok" });

      render(<MultiMarketReplayPanel />);

      const pauseButton = await screen.findByRole("button", { name: /pause recording for us index/i });
      fireEvent.click(pauseButton);

      await waitFor(() =>
        expect(apiMock.pauseStockSimSchedulerEntry).toHaveBeenCalledWith("B:recorder:us:index"),
      );
      await waitFor(() => expect(screen.getByText("default: off")).toBeInTheDocument());
      expect(screen.getByRole("button", { name: /resume recording for us index/i })).toBeInTheDocument();
    });

    it("surfaces a failed toggle instead of silently doing nothing", async () => {
      apiMock.listSchedulerRegistry.mockResolvedValue({
        status: "ok",
        entries: [recordingEntry()],
        sources: {},
      });
      apiMock.pauseStockSimSchedulerEntry.mockRejectedValue(new Error("could not pause (already paused)"));

      render(<MultiMarketReplayPanel />);

      const pauseButton = await screen.findByRole("button", { name: /pause recording for us index/i });
      fireEvent.click(pauseButton);

      await waitFor(() => expect(screen.getByText(/could not pause/i)).toBeInTheDocument());
    });

    it("shows a clear empty state when no recorder categories are reported", async () => {
      apiMock.listSchedulerRegistry.mockResolvedValue({ status: "ok", entries: [], sources: {} });

      render(<MultiMarketReplayPanel />);

      await waitFor(() =>
        expect(screen.getByText(/no recorder categories reported/i)).toBeInTheDocument(),
      );
    });
  });
});
