import { render, screen, fireEvent } from "@testing-library/react";
import { AutonomousAgentCard } from "../AutonomousAgentCard";
import type { AutonomousAgentInstance } from "@/lib/api";

function baseAgent(overrides: Partial<AutonomousAgentInstance> = {}): AutonomousAgentInstance {
  return {
    id: "aa_test",
    name: "Test agent",
    status: "running",
    symbols: ["NIFTY"],
    ...overrides,
  };
}

describe("AutonomousAgentCard — last HOLD decision display", () => {
  it("renders the HOLD rationale and next_check conditions once expanded", () => {
    const agent = baseAgent({
      last_decision: {
        decision: "HOLD",
        confidence: 62,
        rationale: "Hold flat and let the existing watch stand.",
        next_check: {
          conditions: ["NIFTY move 0.5%", "thesis break"],
          min_recheck_sec: 900,
        },
      },
    });

    render(<AutonomousAgentCard agent={agent} onOpen={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /more/i }));

    expect(screen.getByText(/Hold flat and let the existing watch stand\./)).toBeInTheDocument();
    expect(screen.getByText(/NIFTY move 0\.5%, thesis break/)).toBeInTheDocument();
    expect(screen.getByText(/min 15m/)).toBeInTheDocument();
  });

  it("does not render a next_check block for a non-HOLD decision", () => {
    const agent = baseAgent({
      last_decision: {
        decision: "ENTER",
        confidence: 80,
        rationale: "Entering on breakout confirmation.",
        next_check: { conditions: ["should not show"], min_recheck_sec: 60 },
      },
    });

    render(<AutonomousAgentCard agent={agent} onOpen={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /more/i }));

    expect(screen.queryByText(/should not show/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Next check:/)).not.toBeInTheDocument();
  });

  it("does not render a next_check block when the HOLD decision has no watch_spec rules", () => {
    const agent = baseAgent({
      last_decision: {
        decision: "HOLD",
        rationale: "No active watch conditions.",
        next_check: null,
      },
    });

    render(<AutonomousAgentCard agent={agent} onOpen={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /more/i }));

    expect(screen.queryByText(/Next check:/)).not.toBeInTheDocument();
  });
});
