import { render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { ConnectionCenter } from "@/components/portfolio/ConnectionCenter";
import { FactorRegistryGaps } from "@/components/news/FactorRegistryGaps";
import { SourcesPanel } from "@/components/research/SourcesPanel";
import { useProvenanceStore } from "@/stores/provenance";
import { api } from "@/lib/api";

// D410: a reading the server did not send is shown as unknown, never as 0 or as "now".

afterEach(() => vi.restoreAllMocks());

describe("ConnectionCenter", () => {
  it("does not claim '0 connections' before the list has loaded", async () => {
    vi.spyOn(api, "getConnections").mockReturnValue(new Promise(() => {}) as never);
    render(<ConnectionCenter open onClose={() => {}} onChanged={async () => {}} />);
    await screen.findByRole("dialog");
    expect(screen.queryByText(/0 connections/)).not.toBeInTheDocument();
  });

  it("says '0 connections' once the list really is empty", async () => {
    vi.spyOn(api, "getConnections").mockResolvedValue({ profiles: [], connections: [], plugin_directory: "x" } as never);
    render(<ConnectionCenter open onClose={() => {}} onChanged={async () => {}} />);
    expect(await screen.findByText(/0 connections/)).toBeInTheDocument();
  });
});

describe("FactorRegistryGaps", () => {
  it("a candidate without counts shows a dash, not '0 stories · 0 mentions'", async () => {
    vi.spyOn(api, "listFactorRegistryGaps").mockResolvedValue({ candidates: [{ candidate: "mystery_factor" }], counts: {} } as never);
    render(<FactorRegistryGaps />);
    await screen.findByText("mystery_factor");
    expect(screen.queryByText(/0 stories/)).not.toBeInTheDocument();
    expect(screen.queryByText(/0 mentions/)).not.toBeInTheDocument();
  });

  it("tab counts the server did not send are not shown as 0", async () => {
    vi.spyOn(api, "listFactorRegistryGaps").mockResolvedValue({ candidates: [], counts: { pending: 3 } } as never);
    render(<FactorRegistryGaps />);
    await waitFor(() => expect(screen.getByRole("button", { name: /Pending\s*3/ })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Queued\s*—/ })).toBeInTheDocument();
  });
});

describe("SourcesPanel", () => {
  it("shows when a source was retrieved with its date, not a bare time that reads as today", () => {
    useProvenanceStore.setState({
      sources: [{ ref_id: "r1", session_id: "s", display_name: "NSE chain", summary: "", category: "market_data", provider: "nse", retrieved_at: "2026-10-10T04:00:00Z" }],
    } as never);
    render(<SourcesPanel />);
    expect(screen.getByText(/10 Oct 2026, 09:30 IST/)).toBeInTheDocument();
  });
});
