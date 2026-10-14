import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { PositionsBoard } from "../PositionsBoard";
import { api } from "@/lib/api";

const agent = (id: string) => ({ id, name: `Agent ${id}` }) as never;

const group = (overrides: Record<string, unknown> = {}) =>
  ({
    underlying: "NIFTY",
    expiry_days: 5,
    legs_count: 1,
    live_spot: 24500,
    legs: [],
    live_iv_pct: 12.3,
    forecast_horizon_days: 14,
    forecast_as_of: "2026-10-10",
    forecast_stale_days: 0,
    probability_of_profit: 0.5,
    trajectory: [{ pnl_inr: 1200 }],
    pnl_forecast_band: [],
    ...overrides,
  }) as never;

describe("PositionsBoard loud states", () => {
  afterEach(() => vi.restoreAllMocks());

  it("labels a stale forecast band instead of presenting it as live", async () => {
    vi.spyOn(api, "listAutonomousAgents").mockResolvedValue({ agents: [agent("a")] } as never);
    vi.spyOn(api, "getAgentLivePositions").mockResolvedValue({
      groups: [group({ forecast_stale_days: 3 })],
      skipped: [],
    } as never);
    render(<PositionsBoard />);
    expect(await screen.findByText(/Forecast band is 3d stale \(as of 2026-10-10\)/)).toBeInTheDocument();
  });

  it("never keeps the previous agent's positions on screen when the next agent's load fails", async () => {
    vi.spyOn(api, "listAutonomousAgents").mockResolvedValue({ agents: [agent("a"), agent("b")] } as never);
    vi.spyOn(api, "getAgentLivePositions").mockImplementation((id: string) =>
      id === "a"
        ? Promise.resolve({ groups: [group()], skipped: [] } as never)
        : Promise.reject(new Error("boom")),
    );
    render(<PositionsBoard />);
    expect(await screen.findByText(/NIFTY · 5d to expiry/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox"), "b");
    expect(await screen.findByText(/Failed to load live positions/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/NIFTY · 5d to expiry/)).not.toBeInTheDocument());
  });
});
