import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { AgentBoard } from "../AgentBoard";
import { api } from "@/lib/api";

const agent = (id: string) => ({ id, name: `Agent ${id}` }) as never;
const summary = (pnl: number) =>
  ({ pnl_summary: { agent_actual_total_pnl_inr: pnl, shadow_total_pnl_inr: 0, shadow_minus_actual_inr: 0 }, alignment: { alignment_rate: 0.5 } }) as never;

function stubBoard(summaryFor: (id: string) => Promise<unknown>) {
  vi.spyOn(api, "listAutonomousAgents").mockResolvedValue({ agents: [agent("a"), agent("b")] } as never);
  vi.spyOn(api, "getAgentBoardSummary").mockImplementation(summaryFor as never);
  vi.spyOn(api, "getAgentBoardWealthCurve").mockResolvedValue({ points: [] } as never);
  vi.spyOn(api, "getAgentBoardHindsight").mockResolvedValue({ attribution_factor_rollup: [], best_candidate_rank: null } as never);
  vi.spyOn(api, "getAgentBoardHindsightCurves").mockResolvedValue({ curves: [] } as never);
  vi.spyOn(api, "getModelVersionTimeline").mockResolvedValue({ timeline: [] } as never);
  vi.spyOn(api, "getAgentDecisionQuality").mockResolvedValue({ metrics: null } as never);
  vi.spyOn(api, "getAgentGradedDecisions").mockResolvedValue({ decisions: [] } as never);
  vi.spyOn(api, "getPendingWeightProposals").mockResolvedValue({ proposals: [] } as never);
  vi.spyOn(api, "getAppliedWeightProposals").mockResolvedValue({ proposals: [] } as never);
  vi.spyOn(api, "getPendingRetrainProposals").mockResolvedValue({ proposals: [] } as never);
  vi.spyOn(api, "getLearningActivity").mockResolvedValue({ entries: [] } as never);
}

describe("AgentBoard latest-request-wins", () => {
  afterEach(() => vi.restoreAllMocks());

  it("a slow answer for the previous agent never overwrites the newly selected agent's numbers", async () => {
    let releaseA: (v: unknown) => void = () => {};
    stubBoard((id) => (id === "a" ? new Promise((resolve) => { releaseA = resolve; }) : Promise.resolve(summary(222))));
    render(<AgentBoard />);
    await waitFor(() => expect(api.getAgentBoardSummary).toHaveBeenCalledWith("a"));
    const picker = (await screen.findAllByRole("combobox"))[0];
    await userEvent.selectOptions(picker, "b");
    expect(await screen.findByText("₹222")).toBeInTheDocument();
    releaseA(summary(111));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("₹111")).not.toBeInTheDocument();
    expect(screen.getByText("₹222")).toBeInTheDocument();
  });
});
