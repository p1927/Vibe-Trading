import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { vi } from "vitest";
import { NextSimulationPrompt } from "../NextSimulationPrompt";
import { api } from "@/lib/api";
import type { AutonomousAgentInstance, SimulationPromptState } from "@/lib/api";

function agent(overrides: Partial<AutonomousAgentInstance> = {}): AutonomousAgentInstance {
  return { id: "aa_test", name: "Sim agent", status: "stopped", symbols: ["NIFTY"], ...overrides };
}

function promptState(overrides: Partial<SimulationPromptState> = {}): SimulationPromptState {
  return {
    agent_id: "aa_test",
    found: true,
    status: "stopped",
    stop_reason: "simulation_complete",
    simulation_complete: true,
    prompt_pending: true,
    resumable: false,
    completed_run_id: "sim_abcabcabcabc_e1",
    choices: ["same_configuration", "new_configuration", "decline"],
    data_retained: true,
    ...overrides,
  };
}

describe("NextSimulationPrompt", () => {
  afterEach(() => vi.restoreAllMocks());

  it("offers both configurations plus declining when a pass has completed", async () => {
    vi.spyOn(api, "getSimulationState").mockResolvedValue(promptState());

    render(<NextSimulationPrompt agent={agent()} />);

    expect(await screen.findByText(/Simulation complete/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Same configuration/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /New configuration/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /No, stop here/ })).toBeInTheDocument();
  });

  it("never appears for a restart-paused agent — that agent is resumed, not re-run", async () => {
    const spy = vi.spyOn(api, "getSimulationState").mockResolvedValue(promptState());

    const { container } = render(
      <NextSimulationPrompt agent={agent({ status: "paused", pause_reason: "restart" })} />,
    );

    await waitFor(() => expect(container).toBeEmptyDOMElement());
    expect(spy).not.toHaveBeenCalled();
  });

  it("does not appear once the prompt has been answered", async () => {
    vi.spyOn(api, "getSimulationState").mockResolvedValue(
      promptState({ prompt_pending: false, answer: "decline" }),
    );

    const { container } = render(<NextSimulationPrompt agent={agent()} />);

    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("sends consent only for a same-configuration re-run", async () => {
    vi.spyOn(api, "getSimulationState").mockResolvedValue(promptState());
    const answer = vi
      .spyOn(api, "answerNextSimulation")
      .mockResolvedValue({ status: "ok", action: "declined", data_retained: true });

    render(<NextSimulationPrompt agent={agent()} />);
    fireEvent.click(await screen.findByRole("button", { name: /No, stop here/ }));

    await waitFor(() =>
      expect(answer).toHaveBeenCalledWith("aa_test", {
        configuration: "decline",
        consent_ack: false,
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: /Same configuration/ }));
    await waitFor(() =>
      expect(answer).toHaveBeenCalledWith("aa_test", {
        configuration: "same_configuration",
        consent_ack: true,
      }),
    );
  });

  it("stays silent when the endpoint is missing (an older release tier)", async () => {
    vi.spyOn(api, "getSimulationState").mockRejectedValue(new Error("404"));

    const { container } = render(<NextSimulationPrompt agent={agent()} />);

    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});
