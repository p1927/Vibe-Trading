import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";
import { AdvisoryBoard } from "../AdvisoryBoard";
import { api } from "@/lib/api";

const candidate = (name: string) =>
  ({ name, net_ev_inr: 100, max_loss_inr: -50, base_score: 1, risk_reward_ratio: 2, payoff_samples: [], trajectory_band: [] }) as never;
const board = (name: string) =>
  ({ NIFTY: { confidence: { direction: "up", confidence: 0.6, is_stale: false }, candidates: [candidate(name)] } }) as never;

const renderBoard = () => render(<MemoryRouter><AdvisoryBoard /></MemoryRouter>);

describe("AdvisoryBoard live candidates", () => {
  afterEach(() => vi.restoreAllMocks());

  it("a slow earlier poll never overwrites the newer candidates", async () => {
    let releaseFirst: (v: unknown) => void = () => {};
    vi.spyOn(api, "getAdvisoryCandidates")
      .mockImplementationOnce(() => new Promise((resolve) => { releaseFirst = resolve; }))
      .mockResolvedValue(board("Fresh Spread"));
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] }); // only the 60s poll tick is faked
    renderBoard();
    await waitFor(() => expect(api.getAdvisoryCandidates).toHaveBeenCalledTimes(1));
    act(() => { vi.advanceTimersByTime(60_000); }); // the next poll tick answers while the first is still pending
    vi.useRealTimers();
    expect(await screen.findByText("Fresh Spread")).toBeInTheDocument();
    releaseFirst(board("Stale Spread"));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("Stale Spread")).not.toBeInTheDocument();
    expect(screen.getByText("Fresh Spread")).toBeInTheDocument();
  });

  it("a failed refresh never leaves an Approve button on the earlier candidates", async () => {
    vi.spyOn(api, "getAdvisoryCandidates")
      .mockResolvedValueOnce(board("Old Spread"))
      .mockRejectedValue(new Error("boom"));
    renderBoard();
    expect(await screen.findByText("Old Spread")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Refresh now" }));
    expect(await screen.findByText(/Failed to load live candidates/)).toBeInTheDocument();
    expect(screen.queryByText("Old Spread")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });
  it("a double click on Approve & execute places the basket once", async () => {
    vi.spyOn(api, "getAdvisoryCandidates").mockResolvedValue(board("Iron Condor"));
    vi.spyOn(api, "prepareAdvisoryWidget").mockResolvedValue({ widget_id: "w1", orders: [{ symbol: "NIFTY" }] } as never);
    let finish: (v: unknown) => void = () => {};
    const exec = vi.spyOn(api, "executeTradeBasket").mockImplementation((() => new Promise((resolve) => { finish = resolve; })) as never);
    renderBoard();
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await userEvent.dblClick(await screen.findByRole("button", { name: "Approve & execute" }));
    expect(exec).toHaveBeenCalledTimes(1);
    finish({ message: "ok", execution_mode: "paper" });
  });
  it("an exit plan without a profit fraction shows the target day, not a measured 0% profit (D410)", async () => {
    const withPlan = (exit_plan: Record<string, unknown>) =>
      ({ NIFTY: { confidence: { direction: "up", confidence: 0.6, is_stale: false }, candidates: [{ ...candidate("Plan Spread"), exit_plan }] } }) as never;
    vi.spyOn(api, "getAdvisoryCandidates").mockResolvedValue(withPlan({ kind: "target_day", target_day: 5, rationale: "r" }));
    renderBoard();
    const exit = (await screen.findByText(/Exit:/)).textContent ?? "";
    expect(exit).toContain("~D+5");
    expect(exit).not.toMatch(/0% profit/);
  });

  it("a real profit fraction is still shown", async () => {
    vi.spyOn(api, "getAdvisoryCandidates").mockResolvedValue(
      ({ NIFTY: { confidence: { direction: "up", confidence: 0.6, is_stale: false }, candidates: [{ ...candidate("Plan Spread"), exit_plan: { kind: "target_day", target_day: 5, target_fraction_of_max_profit: 0.5, rationale: "r" } }] } }) as never,
    );
    renderBoard();
    expect((await screen.findByText(/Exit:/)).textContent).toContain("~D+5 (50% profit)");
  });
});
