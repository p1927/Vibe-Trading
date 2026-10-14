import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";
import { AutonomousAgentHub } from "../AutonomousAgentHub";
import { AutonomousAgentCard } from "../AutonomousAgentCard";
import { api, type AutonomousAgentInstance } from "@/lib/api";

// Lifting a halt is only ever done by a human after a second, explicit confirmation that says what is being
// cleared (D306: one confirm path - the Agent chat panel, the Hub banner and the agent card all use it).

describe("lifting a live-trading halt always needs the second confirmation", () => {
  afterEach(() => vi.restoreAllMocks());

  it("Hub global halt banner: Resume asks first, Keep halted leaves the halt, Yes resumes once", async () => {
    vi.spyOn(api, "listAutonomousAgents").mockResolvedValue({ agents: [] } as never);
    vi.spyOn(api, "getLiveStatus").mockResolvedValue({
      global_halted: true,
      brokers: [],
      halt: { by: "risk_engine", reason: "daily loss", tripped_at: "2026-10-15T04:00:00Z" },
    } as never);
    const resume = vi.spyOn(api, "resumeLive").mockResolvedValue({ halted: false } as never);
    render(<MemoryRouter><AutonomousAgentHub onCreateAgent={() => {}} /></MemoryRouter>);

    fireEvent.click(await screen.findByRole("button", { name: /Resume live trading/ }));
    expect(resume).not.toHaveBeenCalled();
    expect(screen.getByText(/Clear the kill switch/)).toBeInTheDocument();
    expect(screen.getByTestId("halt-detail")).toHaveTextContent(/risk_engine/);

    fireEvent.click(screen.getByRole("button", { name: "Keep halted" }));
    expect(resume).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Resume live trading/ }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, resume" }));
    await waitFor(() => expect(resume).toHaveBeenCalledTimes(1));
  });

  const agent = (overrides: Partial<AutonomousAgentInstance>) =>
    ({ id: "aa_1", name: "A", status: "paused", symbols: ["NIFTY"], ...overrides }) as AutonomousAgentInstance;

  it("agent card with a trading halt: Resume asks first and only the confirmation lifts it", () => {
    const onResume = vi.fn();
    render(<AutonomousAgentCard agent={agent({ trading_halted: true })} onOpen={() => {}} onResume={onResume} />);
    fireEvent.click(screen.getByRole("button", { name: /more/i }));
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(onResume).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Yes, resume" }));
    expect(onResume).toHaveBeenCalledTimes(1);
  });

  it("agent card that is only paused (no halt) still resumes with one click", () => {
    const onResume = vi.fn();
    render(<AutonomousAgentCard agent={agent({ trading_halted: false })} onOpen={() => {}} onResume={onResume} />);
    fireEvent.click(screen.getByRole("button", { name: /more/i }));
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(onResume).toHaveBeenCalledTimes(1);
  });
});
