import { render, screen } from "@testing-library/react";
import { StackHealthStrip } from "../AutonomousAgentHub";
import type { AutonomousStackHealth } from "@/lib/api";

function baseHealth(overrides: Partial<AutonomousStackHealth> = {}): AutonomousStackHealth {
  return {
    scheduler_health: "ok",
    nautilus_watch_enabled: false,
    agent_trading_enabled: true,
    ...overrides,
  };
}

describe("StackHealthStrip — tradability reason", () => {
  it("renders cannot_trade_reason with the broker reconnect hint when the broker session is dead", () => {
    render(
      <StackHealthStrip
        health={baseHealth({ can_trade: false, cannot_trade_reason: "broker_session_dead" })}
      />,
    );

    expect(screen.getByText(/broker session dead/)).toBeInTheDocument();
    expect(screen.getByText(/log in to the broker in the OpenAlgo UI/)).toBeInTheDocument();
  });

  it("says OpenAlgo is not responding, not a token problem, when it is unreachable", () => {
    render(
      <StackHealthStrip
        health={baseHealth({ can_trade: false, cannot_trade_reason: "openalgo_unreachable" })}
      />,
    );

    expect(screen.getByText(/OpenAlgo is not responding/)).toBeInTheDocument();
    expect(screen.queryByText(/pasted/)).not.toBeInTheDocument();
  });

  it("does not render a cannot-trade chip when can_trade is true", () => {
    render(<StackHealthStrip health={baseHealth({ can_trade: true, cannot_trade_reason: null })} />);

    expect(screen.queryByText(/cannot trade/)).not.toBeInTheDocument();
  });
});
