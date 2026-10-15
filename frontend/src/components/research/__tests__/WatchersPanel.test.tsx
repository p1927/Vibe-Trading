import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { WatchersLiveProvider, WatchersPanel } from "../WatchersPanel";

const apiMock = vi.hoisted(() => ({
  getWatchesLive: vi.fn(),
  getAutonomousAgent: vi.fn(),
  deleteWatch: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WatchersPanel", () => {
  it("shares one polling state when two views show the same agent", async () => {
    apiMock.getWatchesLive.mockResolvedValue({
      watches: [],
      fetched_at: "2026-08-27T00:00:00Z",
      market_open: true,
      status: "ok",
      quotes_ok: true,
    });
    apiMock.getAutonomousAgent.mockResolvedValue({ id: "agent-1", watch_spec: null });

    render(
      <WatchersLiveProvider agentId="agent-1">
        <WatchersPanel agentId="agent-1" />
        <WatchersPanel sessionId="session-1" agentId="agent-1" />
      </WatchersLiveProvider>,
    );

    await waitFor(() => {
      expect(apiMock.getWatchesLive).toHaveBeenCalledTimes(1);
      expect(apiMock.getAutonomousAgent).toHaveBeenCalledTimes(1);
    });
  });

  it("removing a watch rule asks first; cancel keeps it, confirm deletes it", async () => {
    apiMock.getWatchesLive.mockResolvedValue({
      watches: [{ watch_id: "w1", label: "NIFTY breakout", rules: [] }],
      fetched_at: "2026-08-27T00:00:00Z",
      market_open: true,
      status: "ok",
      quotes_ok: true,
    });
    apiMock.getAutonomousAgent.mockResolvedValue({ id: "agent-1", watch_spec: null });
    apiMock.deleteWatch.mockResolvedValue(undefined);
    render(<WatchersPanel agentId="agent-1" />);
    fireEvent.click(await screen.findByTitle("Remove watch"));
    expect(apiMock.deleteWatch).not.toHaveBeenCalled();
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    expect(apiMock.deleteWatch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTitle("Remove watch"));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove watch" }));
    await waitFor(() => expect(apiMock.deleteWatch).toHaveBeenCalledWith("w1"));
  });
});
