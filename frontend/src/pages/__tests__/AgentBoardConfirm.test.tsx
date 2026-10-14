import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentBoard } from "../AgentBoard";
import { api } from "@/lib/api";

const weight = { id: "w1", weight_id: "momentum", created_at: "2026-10-15T04:00:00Z", status: "pending", current_value: 0.2, proposed_value: 0.3, rationale: "r" };
const retrain = {
  id: "r1", created_at: "2026-10-15T04:00:00Z", status: "pending", reason: "drift",
  diff: { previous_mae: 1, candidate_mae: 0.9, features_added: [], features_removed: [], horizon_name: "h" },
};

function stubBoard() {
  vi.spyOn(api, "listAutonomousAgents").mockResolvedValue({ agents: [{ id: "a", name: "Agent a" }] } as never);
  vi.spyOn(api, "getAgentBoardSummary").mockResolvedValue({ pnl_summary: {}, alignment: {} } as never);
  vi.spyOn(api, "getAgentBoardWealthCurve").mockResolvedValue({ points: [] } as never);
  vi.spyOn(api, "getAgentBoardHindsight").mockResolvedValue({ attribution_factor_rollup: [], best_candidate_rank: null } as never);
  vi.spyOn(api, "getAgentBoardHindsightCurves").mockResolvedValue({ curves: [] } as never);
  vi.spyOn(api, "getModelVersionTimeline").mockResolvedValue({ timeline: [] } as never);
  vi.spyOn(api, "getAgentDecisionQuality").mockResolvedValue({ metrics: null } as never);
  vi.spyOn(api, "getAgentGradedDecisions").mockResolvedValue({ decisions: [] } as never);
  vi.spyOn(api, "getPendingWeightProposals").mockResolvedValue({ proposals: [weight] } as never);
  vi.spyOn(api, "getAppliedWeightProposals").mockResolvedValue({ proposals: [{ ...weight, id: "w2", status: "applied" }] } as never);
  vi.spyOn(api, "getPendingRetrainProposals").mockResolvedValue({ proposals: [retrain] } as never);
  vi.spyOn(api, "getLearningActivity").mockResolvedValue({ entries: [] } as never);
  vi.spyOn(api, "applyWeightProposal").mockResolvedValue({} as never);
  vi.spyOn(api, "revertWeightProposal").mockResolvedValue({} as never);
  vi.spyOn(api, "applyRetrainProposal").mockResolvedValue({} as never);
}

// A weight / retrain change moves a live agent: one click must only ask, the confirm acts (one confirm path).
describe("AgentBoard proposal changes need a confirm", () => {
  beforeEach(() => {
    stubBoard();
    vi.spyOn(window, "prompt").mockReturnValue("because");
  });
  afterEach(() => vi.restoreAllMocks());

  const open = async (name: string, index = 0) => {
    render(<AgentBoard />);
    const buttons = await screen.findAllByRole("button", { name });
    await userEvent.click(buttons[index]);
  };
  const confirmBtn = async () => within(await screen.findByRole("alertdialog")).getByRole("button", { name: /^Confirm/ });

  it("Apply weight: the click asks, Cancel does nothing, Confirm applies exactly once", async () => {
    await open("Apply", 0);
    expect(api.applyWeightProposal).not.toHaveBeenCalled();
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(api.applyWeightProposal).not.toHaveBeenCalled();
    await userEvent.click(screen.getAllByRole("button", { name: "Apply" })[0]);
    await userEvent.dblClick(await confirmBtn());
    await waitFor(() => expect(api.applyWeightProposal).toHaveBeenCalledTimes(1));
    expect(api.applyWeightProposal).toHaveBeenCalledWith("w1");
  });

  it("Revert: the click asks before even prompting for the reason", async () => {
    await open("Revert");
    expect(api.revertWeightProposal).not.toHaveBeenCalled();
    expect(window.prompt).not.toHaveBeenCalled();
    await userEvent.click(await confirmBtn());
    await waitFor(() => expect(api.revertWeightProposal).toHaveBeenCalledWith("w2", "because"));
  });

  it("Apply retrain proposal asks first too", async () => {
    await open("Apply", 1);
    expect(api.applyRetrainProposal).not.toHaveBeenCalled();
    await userEvent.click(await confirmBtn());
    await waitFor(() => expect(api.applyRetrainProposal).toHaveBeenCalledWith("r1"));
  });
});
