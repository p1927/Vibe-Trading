import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ContextDrawer } from "../ContextDrawer";
import { useProvenanceStore } from "@/stores/provenance";

const apiMock = vi.hoisted(() => ({ getSessionProvenance: vi.fn(), getHubPlan: vi.fn(), runAgentDebate: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: apiMock }));
vi.mock("@/components/research/SourcesPanel", () => ({ SourcesPanel: () => <div>sources-panel</div> }));
vi.mock("@/components/research/WatchersPanel", () => ({ WatchersPanel: () => null }));
vi.mock("@/components/research/AgentDebatePanel", () => ({
  AgentDebatePanel: ({ onRunDebate }: { onRunDebate?: () => void }) => (
    <button type="button" onClick={onRunDebate}>
      run-debate
    </button>
  ),
}));
vi.mock("@/components/research/ResearchContextPanel", () => ({
  ResearchContextPanel: ({ underlying }: { underlying: string }) => <div>{`plan:${underlying}`}</div>,
}));
vi.mock("sonner", () => ({ toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() } }));

afterEach(() => cleanup());
beforeEach(() => {
  vi.clearAllMocks();
  apiMock.getSessionProvenance.mockResolvedValue({ sources: [] });
  apiMock.getHubPlan.mockResolvedValue({ status: "ok", artifact: { underlying: "NIFTY" } });
  useProvenanceStore.setState({ drawerOpen: true, activeSection: "research" });
});

describe("ContextDrawer", () => {
  it("does not leave the previous symbol's plan on screen when the next symbol has none", async () => {
    const { rerender } = render(<ContextDrawer sessionId={null} ticker="NIFTY" />);
    expect(await screen.findByText("plan:NIFTY")).toBeInTheDocument();

    apiMock.getHubPlan.mockResolvedValue({ status: "not_found", message: "No hub plan" });
    rerender(<ContextDrawer sessionId={null} ticker="RELIANCE" />);
    expect(await screen.findByText(/No hub plan yet/)).toBeInTheDocument();
    expect(screen.queryByText("plan:NIFTY")).toBeNull();
  });

  it("shows a provenance load failure in the Sources section instead of an empty list", async () => {
    apiMock.getSessionProvenance.mockRejectedValue(new Error("provenance 500"));
    useProvenanceStore.setState({ activeSection: "sources" });
    render(<ContextDrawer sessionId="s1" ticker={null} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("provenance 500");
  });

  it("survives a browser that throws on localStorage writes", async () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    try {
      await act(async () => {
        render(<ContextDrawer sessionId={null} ticker={null} />);
      });
      expect(screen.getByText("Context")).toBeInTheDocument();
    } finally {
      spy.mockRestore();
    }
  });

  it("a session with no sources does not keep the previous session's sources", async () => {
    useProvenanceStore.setState({ activeSection: "sources", sources: [] });
    apiMock.getSessionProvenance.mockResolvedValueOnce({ sources: [{ ref_id: "a", label: "A" }] });
    const { rerender } = render(<ContextDrawer sessionId="s1" ticker={null} />);
    await act(async () => {});
    expect(useProvenanceStore.getState().sources).toHaveLength(1);

    apiMock.getSessionProvenance.mockResolvedValueOnce({ sources: [] });
    rerender(<ContextDrawer sessionId="s2" ticker={null} />);
    await act(async () => {});
    expect(useProvenanceStore.getState().sources).toEqual([]);
  });

  it("a slow provenance answer for the previous session cannot overwrite the current one", async () => {
    useProvenanceStore.setState({ activeSection: "sources", sources: [] });
    let late!: (value: unknown) => void;
    apiMock.getSessionProvenance.mockReturnValueOnce(new Promise((resolve) => { late = resolve; }));
    const { rerender } = render(<ContextDrawer sessionId="s1" ticker={null} />);
    apiMock.getSessionProvenance.mockResolvedValueOnce({ sources: [{ ref_id: "b", label: "B" }] });
    rerender(<ContextDrawer sessionId="s2" ticker={null} />);
    await act(async () => {});
    await act(async () => late({ sources: [{ ref_id: "old", label: "OLD" }] }));
    expect(useProvenanceStore.getState().sources.map((s) => s.ref_id)).toEqual(["b"]);
  });

  it("a debate answer for the previous symbol is not shown under the new symbol", async () => {
    useProvenanceStore.setState({ activeSection: "debate" });
    let finish!: (value: unknown) => void;
    apiMock.runAgentDebate.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    const onDebateUpdate = vi.fn();
    const { rerender } = render(<ContextDrawer sessionId={null} ticker="NIFTY" onDebateUpdate={onDebateUpdate} />);
    fireEvent.click(screen.getByText("run-debate"));
    rerender(<ContextDrawer sessionId={null} ticker="RELIANCE" onDebateUpdate={onDebateUpdate} />);
    onDebateUpdate.mockClear();
    await act(async () => finish({ status: "ok", debate: { rating: "buy" } }));
    expect(onDebateUpdate).not.toHaveBeenCalled();
  });

  it("two quick clicks on Run debate start one debate", async () => {
    useProvenanceStore.setState({ activeSection: "debate" });
    apiMock.runAgentDebate.mockReturnValue(new Promise(() => {}));
    render(<ContextDrawer sessionId={null} ticker="NIFTY" onDebateUpdate={vi.fn()} />);
    fireEvent.click(screen.getByText("run-debate"));
    fireEvent.click(screen.getByText("run-debate"));
    expect(apiMock.runAgentDebate).toHaveBeenCalledTimes(1);
  });
});
