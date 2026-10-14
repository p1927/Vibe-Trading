import { render, screen } from "@testing-library/react";
import { IndexCard } from "../IndexCard";

const base = { name: "NIFTY 50", price: 24500, changePct: null, sparkline: [] as number[] };

// D410: an index with no previous close has an unknown change - never a green "up".
describe("IndexCard change colour", () => {
  it("a missing change is neutral (em dash, neither green nor red)", () => {
    render(<IndexCard {...base} change={null} />);
    const change = screen.getByText("—");
    expect(change.className).not.toMatch(/emerald|red-/);
  });

  it("a real gain is green and a real loss is red", () => {
    const { rerender } = render(<IndexCard {...base} change={12.5} changePct={0.05} />);
    expect(screen.getByText(/\+12\.5/).className).toMatch(/emerald/);
    rerender(<IndexCard {...base} change={-3} changePct={-0.01} />);
    expect(screen.getByText(/-3/).className).toMatch(/red-/);
  });

  it("a genuine zero change is neutral-up (green), not hidden", () => {
    render(<IndexCard {...base} change={0} changePct={0} />);
    expect(screen.getByText(/^\+0 \+0\.00%$/).className).toMatch(/emerald/);
  });
});
