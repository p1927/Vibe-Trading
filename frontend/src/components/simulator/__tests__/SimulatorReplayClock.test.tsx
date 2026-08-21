import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SimulatorReplayClock } from "../SimulatorReplayClock";

const apiMock = vi.hoisted(() => ({
  getReplayStatus: vi.fn(),
  pauseReplay: vi.fn(),
  resumeReplay: vi.fn(),
  stopReplay: vi.fn(),
  seekReplay: vi.fn(),
  getHubIndexHistoryBars: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));

function statusPayload(overrides: Record<string, unknown> = {}, { mode = "replay" } = {}) {
  return {
    replay: {
      mode,
      clock: {
        replay_date: "2024-04-15",
        sim_now: "2024-04-15T10:00:00+05:30",
        speed: 1,
        loop: true,
        paused: false,
        completed: false,
        ...overrides,
      },
    },
  };
}

describe("SimulatorReplayClock", () => {
  beforeEach(() => {
    apiMock.getReplayStatus.mockReset();
    apiMock.pauseReplay.mockReset();
    apiMock.resumeReplay.mockReset();
    apiMock.stopReplay.mockReset();
    apiMock.seekReplay.mockReset();
    apiMock.getHubIndexHistoryBars.mockReset();
    // Default: no bars — coverage layer stays transparent.
    apiMock.getHubIndexHistoryBars.mockResolvedValue({ status: "ok", bars: [] });
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the arm-first hint and does not poll status when armedRange is null", () => {
    render(<SimulatorReplayClock armedRange={null} onStop={() => {}} />);
    expect(screen.getByText(/Select a day/i)).toBeInTheDocument();
    expect(apiMock.getReplayStatus).not.toHaveBeenCalled();
  });

  it("polls status and renders Pause once armed", async () => {
    apiMock.getReplayStatus.mockResolvedValue(statusPayload());
    render(<SimulatorReplayClock armedRange={{ start: "2024-04-15", end: "2024-04-15" }} onStop={() => {}} />);

    await waitFor(() => {
      expect(apiMock.getReplayStatus).toHaveBeenCalled();
    });
    expect(await screen.findByTestId("simulator-pause")).toBeInTheDocument();
    expect(screen.queryByTestId("simulator-resume")).toBeNull();
  });

  it("clicking Pause calls api.pauseReplay and flips to Resume", async () => {
    apiMock.getReplayStatus.mockResolvedValue(statusPayload());
    apiMock.pauseReplay.mockResolvedValue(statusPayload({ paused: true }));
    render(<SimulatorReplayClock armedRange={{ start: "2024-04-15", end: "2024-04-15" }} onStop={() => {}} />);

    const pauseBtn = await screen.findByTestId("simulator-pause");
    fireEvent.click(pauseBtn);

    await waitFor(() => {
      expect(apiMock.pauseReplay).toHaveBeenCalledTimes(1);
    });
    expect(await screen.findByTestId("simulator-resume")).toBeInTheDocument();
  });

  it("clicking Stop calls api.stopReplay and invokes onStop", async () => {
    apiMock.getReplayStatus.mockResolvedValue(statusPayload());
    apiMock.stopReplay.mockResolvedValue({ replay: null });
    const onStop = vi.fn();
    render(<SimulatorReplayClock armedRange={{ start: "2024-04-15", end: "2024-04-15" }} onStop={onStop} />);

    const stopBtn = await screen.findByTestId("simulator-stop");
    fireEvent.click(stopBtn);

    await waitFor(() => {
      expect(apiMock.stopReplay).toHaveBeenCalledTimes(1);
      expect(onStop).toHaveBeenCalledTimes(1);
    });
  });

  it("surfaces an error message when pause fails", async () => {
    apiMock.getReplayStatus.mockResolvedValue(statusPayload());
    apiMock.pauseReplay.mockRejectedValue(new Error("network down"));
    render(<SimulatorReplayClock armedRange={{ start: "2024-04-15", end: "2024-04-15" }} onStop={() => {}} />);

    const pauseBtn = await screen.findByTestId("simulator-pause");
    fireEvent.click(pauseBtn);

    expect(await screen.findByText("network down")).toBeInTheDocument();
  });

  it("fetches index bars for the armed day to compute the coverage layer", async () => {
    apiMock.getReplayStatus.mockResolvedValue(statusPayload());
    render(<SimulatorReplayClock armedRange={{ start: "2024-04-15", end: "2024-04-15" }} onStop={() => {}} />);

    await waitFor(() => {
      expect(apiMock.getHubIndexHistoryBars).toHaveBeenCalledTimes(1);
    });
    const call = apiMock.getHubIndexHistoryBars.mock.calls[0][0];
    expect(call.symbol).toBe("NIFTY");
    expect(call.exchange).toBe("NSE_INDEX");
    expect(call.since_ist).toBe("2024-04-15T09:15:00+05:30");
    expect(call.until_ist).toBe("2024-04-15T15:30:00+05:30");
  });

  it("paints emerald cells where bars exist and skips gaps", async () => {
    apiMock.getReplayStatus.mockResolvedValue(
      statusPayload({ sim_now: "2024-04-15T09:30:00+05:30" })
    );
    // Two bars: 09:20–09:21 and 09:25–09:26. Everything else is a gap.
    apiMock.getHubIndexHistoryBars.mockResolvedValue({
      status: "ok",
      bars: [
        {
          ts_ist: "2024-04-15T09:20:00+05:30",
          bar_minutes: 1,
          symbol: "NIFTY",
          exchange: "NSE_INDEX",
          trading_day: "2024-04-15",
          open: 0, high: 0, low: 0, close: 0, volume: 0,
          prev_close: null, source: "test",
        },
        {
          ts_ist: "2024-04-15T09:25:00+05:30",
          bar_minutes: 1,
          symbol: "NIFTY",
          exchange: "NSE_INDEX",
          trading_day: "2024-04-15",
          open: 0, high: 0, low: 0, close: 0, volume: 0,
          prev_close: null, source: "test",
        },
      ],
    });

    render(<SimulatorReplayClock armedRange={{ start: "2024-04-15", end: "2024-04-15" }} onStop={() => {}} />);

    const track = await screen.findByTestId("simulator-coverage-track");
    await waitFor(() => {
      const cells = track.querySelectorAll('[data-testid="simulator-coverage-cell"]');
      expect(cells.length).toBeGreaterThan(0);
    });
    const cells = Array.from(
      track.querySelectorAll('[data-testid="simulator-coverage-cell"]')
    ) as HTMLDivElement[];
    const coveredCells = cells.filter((c) => c.getAttribute("data-covered") === "1");
    const gapCells = cells.filter((c) => c.getAttribute("data-covered") === "0");
    expect(coveredCells).toHaveLength(2);
    expect(gapCells.length).toBeGreaterThan(0); // there must be at least one gap range
    // The covered cells are at 09:20 and 09:25 — minutes 560 and 565.
    // 560 − 555 = 5 minutes into the session → 5 * (100/375) ≈ 1.33%
    expect(coveredCells[0].style.left).toBe("1.33%");
    expect(coveredCells[0].className).toContain("bg-emerald-500");
    // Gap cells (the trailing transparent fill) must be transparent, not emerald.
    for (const c of gapCells) {
      expect(c.className).not.toContain("bg-emerald-500");
    }
  });

  it("shows a desync warning (not a stale Running clock) when the server disagrees", async () => {
    // Client thinks it's armed for 2024-04-15, but the server reports
    // mode !== "replay" — e.g. the standalone service restarted with no
    // persisted arm. Must not render Pause/Running as if all were well.
    apiMock.getReplayStatus.mockResolvedValue(statusPayload({}, { mode: "live" }));
    render(<SimulatorReplayClock armedRange={{ start: "2024-04-15", end: "2024-04-15" }} onStop={() => {}} />);

    expect(await screen.findByText(/isn't actually replaying/i)).toBeInTheDocument();
    expect(screen.queryByTestId("simulator-pause")).toBeNull();
    expect(screen.queryByTestId("simulator-stop")).toBeNull();
  });

  it("clicking Reset on the desync warning calls onStop", async () => {
    apiMock.getReplayStatus.mockResolvedValue(statusPayload({}, { mode: "" }));
    const onStop = vi.fn();
    render(<SimulatorReplayClock armedRange={{ start: "2024-04-15", end: "2024-04-15" }} onStop={onStop} />);

    const resetBtn = await screen.findByTestId("simulator-reset-desync");
    fireEvent.click(resetBtn);
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("does not show the desync warning before the first status poll resolves", () => {
    apiMock.getReplayStatus.mockReturnValue(new Promise(() => {})); // never resolves
    render(<SimulatorReplayClock armedRange={{ start: "2024-04-15", end: "2024-04-15" }} onStop={() => {}} />);
    expect(screen.queryByText(/isn't actually replaying/i)).toBeNull();
  });

  it("does not paint any emerald cells when no bars are returned", async () => {
    apiMock.getReplayStatus.mockResolvedValue(statusPayload());
    apiMock.getHubIndexHistoryBars.mockResolvedValue({ status: "ok", bars: [] });

    render(<SimulatorReplayClock armedRange={{ start: "2024-04-15", end: "2024-04-15" }} onStop={() => {}} />);

    const track = await screen.findByTestId("simulator-coverage-track");
    await waitFor(() => {
      expect(apiMock.getHubIndexHistoryBars).toHaveBeenCalled();
    });
    // No emerald-painted cells — only the implicit transparent full-day range.
    const coveredCells = track.querySelectorAll(
      '[data-testid="simulator-coverage-cell"][data-covered="1"]'
    );
    expect(coveredCells.length).toBe(0);
  });
});
