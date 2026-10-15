import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";
import { AutonomousAgentHub } from "../AutonomousAgentHub";
import { AgentMemoryPanel } from "../AgentMemoryPanel";
import { ApiError, api } from "@/lib/api";

// One confirm style (D306): destructive hub/memory actions ask through ConfirmDialog, never window.confirm.

describe("AutonomousAgentHub delete", () => {
  afterEach(() => vi.restoreAllMocks());

  function mount() {
    vi.spyOn(api, "listAutonomousAgents").mockResolvedValue({
      agents: [{ id: "aa_1", name: "Alpha", status: "paused", symbols: ["NIFTY"] }],
    } as never);
    vi.spyOn(api, "getLiveStatus").mockResolvedValue({ global_halted: false } as never);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    return render(
      <MemoryRouter>
        <AutonomousAgentHub onCreateAgent={() => {}} />
      </MemoryRouter>,
    );
  }

  async function openDelete() {
    fireEvent.click(await screen.findByRole("button", { name: /more/i }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
  }

  it("does not delete when the dialog is cancelled, and never uses window.confirm", async () => {
    const del = vi.spyOn(api, "deleteAutonomousAgent").mockResolvedValue(undefined as never);
    mount();
    await openDelete();
    expect(screen.getByRole("alertdialog")).toHaveTextContent(/Delete this autonomous agent/);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(del).not.toHaveBeenCalled();
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it("a 409 open-positions delete asks twice (delete, then flatten) and flattens only after both yes", async () => {
    const del = vi
      .spyOn(api, "deleteAutonomousAgent")
      .mockRejectedValueOnce(new ApiError("open positions", 409, { error: "open_positions" }))
      .mockResolvedValue(undefined as never);
    mount();
    await openDelete();
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.getByRole("alertdialog")).toHaveTextContent(/open positions/));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete and flatten" }));
    await waitFor(() => expect(screen.getByRole("alertdialog")).toHaveTextContent(/flatten agent-scoped positions/));
    expect(del).toHaveBeenCalledTimes(1);
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Flatten and delete" }));
    await waitFor(() => expect(del).toHaveBeenLastCalledWith("aa_1", { flattenPositions: true }));
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it("declining the flatten question deletes nothing", async () => {
    const del = vi
      .spyOn(api, "deleteAutonomousAgent")
      .mockRejectedValueOnce(new ApiError("open positions", 409, { error: "open_positions" }));
    mount();
    await openDelete();
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.getByRole("alertdialog")).toHaveTextContent(/open positions/));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(del).toHaveBeenCalledTimes(1);
  });
});

describe("AgentMemoryPanel archive", () => {
  afterEach(() => vi.restoreAllMocks());

  it("clears an entry only after the dialog is confirmed, not through window.confirm", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(api, "listAgentMemory").mockResolvedValue({
      entries: [{ id: "m1", title: "Note", description: "d", memory_type: "fact", updated_at: 1, importance: 1, quality_score: 1, access_count: 0, last_accessed: 0, agent_id: "aa_1" }],
    } as never);
    vi.spyOn(api, "getMemoryEntry").mockResolvedValue({
      id: "m1", title: "Note", description: "d", memory_type: "fact", updated_at: 1, body: "body",
    } as never);
    const archive = vi.spyOn(api, "archiveMemoryEntry").mockResolvedValue(undefined as never);
    render(<AgentMemoryPanel agentId="aa_1" />);
    fireEvent.click(await screen.findByText("Note"));
    fireEvent.click(await screen.findByRole("button", { name: "Clear from memory" }));
    expect(archive).not.toHaveBeenCalled();
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
    expect(archive).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Clear from memory" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Clear entry" }));
    await waitFor(() => expect(archive).toHaveBeenCalledWith("m1"));
    expect(window.confirm).not.toHaveBeenCalled();
  });
});
