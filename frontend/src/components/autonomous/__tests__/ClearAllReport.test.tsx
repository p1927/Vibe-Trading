import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";
import { AutonomousAgentHub, summarizeClearAll } from "../AutonomousAgentHub";
import { ApiError, api } from "@/lib/api";
import type { ClearAllAutonomousAgentsResponse } from "@/lib/api";

function response(overrides: Partial<ClearAllAutonomousAgentsResponse> = {}): ClearAllAutonomousAgentsResponse {
  return {
    status: "ok",
    stopped: ["aa_1"],
    deleted: ["aa_1"],
    remaining_count: 0,
    flatten: { aa_1: { status: "ok", remaining_positions: 0 } },
    unowned_positions: [],
    book_error: null,
    errors: [],
    nautilus: {},
    ...overrides,
  };
}

describe("summarizeClearAll", () => {
  it("is clean only for status ok with nothing left", () => {
    const report = summarizeClearAll(response());
    expect(report).toEqual({ clean: true, removed: 1, problems: [], unowned: [] });
  });

  it("an ok status with positions left on the book is NOT clean: they are listed, not hidden", () => {
    const report = summarizeClearAll(
      response({ unowned_positions: [{ symbol: "NIFTY24OCT25000CE", exchange: "NFO", quantity: 75, product: "NRML" }] }),
    );
    expect(report.clean).toBe(false);
    expect(report.unowned).toEqual(["NIFTY24OCT25000CE (NFO) x 75 NRML"]);
  });

  it("an unreadable book says UNKNOWN, not flat", () => {
    const report = summarizeClearAll(response({ status: "partial", book_error: "openalgo 502" }));
    expect(report.clean).toBe(false);
    expect(report.problems.join(" ")).toMatch(/UNKNOWN, not flat/);
  });

  it("each per-agent flatten that did not finish, each stop/delete error and a Nautilus error is spelled out", () => {
    const report = summarizeClearAll(
      response({
        status: "partial",
        remaining_count: 1,
        flatten: { aa_1: { status: "partial", remaining_positions: 2 }, aa_2: { status: "error", error: "book down" } },
        errors: [{ agent_id: "aa_3", phase: "delete", error: "boom" }],
        nautilus: { error: "watch would not stop" },
      }),
    );
    const text = report.problems.join("\n");
    expect(text).toMatch(/aa_1 flatten partial.*2 position/);
    expect(text).toMatch(/aa_2 flatten error: book down/);
    expect(text).toMatch(/aa_3 delete: boom/);
    expect(text).toMatch(/1 agent\(s\) were not deleted/);
    expect(text).toMatch(/Nautilus watch: watch would not stop/);
  });

  it("handles the shapes the book really returns: tradingsymbol + netqty strings, a null flatten outcome, a symbol-less row", () => {
    const report = summarizeClearAll(
      response({
        flatten: { aa_1: null },
        unowned_positions: [{ tradingsymbol: "SBIN", netqty: "-50" }, {}],
      }),
    );
    expect(report.unowned).toEqual(["SBIN x -50", "unknown symbol"]);
    expect(report.problems).toEqual([]);
    expect(report.clean).toBe(false);
  });

  it("a partial status with no other detail still reports a problem", () => {
    expect(summarizeClearAll(response({ status: "partial" })).problems).toEqual(["status partial"]);
  });
});

describe("AutonomousAgentHub clear-all report", () => {
  afterEach(() => vi.restoreAllMocks());

  function mount() {
    vi.spyOn(api, "listAutonomousAgents").mockResolvedValue({
      agents: [{ id: "aa_1", name: "A", status: "running", symbols: ["NIFTY"] }],
    });
    vi.spyOn(api, "getLiveStatus").mockResolvedValue({ global_halted: false } as never);
    vi.spyOn(window, "confirm").mockReturnValue(false);
    return render(
      <MemoryRouter>
        <AutonomousAgentHub onCreateAgent={() => {}} />
      </MemoryRouter>,
    );
  }

  it("shows unowned positions and problems persistently after a partial clear-all", async () => {
    vi.spyOn(api, "clearAllAutonomousAgents").mockResolvedValue(
      response({ status: "partial", unowned_positions: [{ symbol: "RELIANCE", exchange: "NSE", quantity: 10 }] }),
    );
    mount();
    const button = await screen.findByRole("button", { name: /clear all agents/i });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    fireEvent.click(await screen.findByRole("button", { name: "Clear all" }));
    expect(window.confirm).not.toHaveBeenCalled();
    const unowned = await screen.findByTestId("clear-all-unowned");
    expect(unowned).toHaveTextContent("RELIANCE (NSE) x 10");
    expect(screen.getByTestId("clear-all-report")).toHaveTextContent(/did not finish cleanly/);
  });

  it("an unreachable live-status read is announced instead of leaving the halt banner silently absent", async () => {
    vi.spyOn(api, "listAutonomousAgents").mockResolvedValue({ agents: [] });
    vi.spyOn(api, "getLiveStatus").mockRejectedValue(new ApiError("broker status 503", 503));
    render(
      <MemoryRouter>
        <AutonomousAgentHub onCreateAgent={() => {}} />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId("live-status-error")).toHaveTextContent(/halt state unknown.*503/));
  });
});
