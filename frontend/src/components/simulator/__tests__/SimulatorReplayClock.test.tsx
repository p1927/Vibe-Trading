import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SimulatorReplayClock } from "../SimulatorReplayClock";

const apiMock = vi.hoisted(() => ({
  getReplayStatus: vi.fn(),
  pauseReplay: vi.fn(),
  resumeReplay: vi.fn(),
  stopReplay: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));

function statusPayload(overrides: Record<string, unknown> = {}) {
  return {
    replay: {
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
});
