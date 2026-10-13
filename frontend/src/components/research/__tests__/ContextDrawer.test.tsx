import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ContextDrawer } from "../ContextDrawer";
import { useProvenanceStore } from "@/stores/provenance";

const apiMock = vi.hoisted(() => ({ getSessionProvenance: vi.fn(), getHubPlan: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: apiMock }));
vi.mock("@/components/research/SourcesPanel", () => ({ SourcesPanel: () => <div>sources-panel</div> }));
vi.mock("@/components/research/WatchersPanel", () => ({ WatchersPanel: () => null }));
vi.mock("@/components/research/AgentDebatePanel", () => ({ AgentDebatePanel: () => null }));
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
});
