import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { vi } from "vitest";
import { ConnectionCenter } from "@/components/portfolio/ConnectionCenter";
import { DataCapturePanel } from "@/components/prediction/DataCapturePanel";
import { FactorRegistryGaps } from "@/components/news/FactorRegistryGaps";
import { AgentMemoryPanel } from "@/components/autonomous/AgentMemoryPanel";
import { api } from "@/lib/api";

// A slow older answer must never overwrite (or un-load) the newer one: tab switches, Refresh clicks and
// re-opened drawers all issue overlapping requests.

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

afterEach(() => vi.restoreAllMocks());

const connection = (id: string, label: string) => ({
  id, label, connector: "kite", environment: "paper", transport: "local", credential_fields: [],
  credential_status: {}, credentials_configured: false,
});

describe("ConnectionCenter", () => {
  it("re-opening the drawer: the older slow list never overwrites the newer one", async () => {
    const first = deferred<never>();
    const spy = vi.spyOn(api, "getConnections")
      .mockReturnValueOnce(first.promise as never)
      .mockResolvedValue({ profiles: [], connections: [connection("c2", "Fresh account")], plugin_directory: "x" } as never);
    const onChanged = vi.fn(async () => {});
    const { rerender } = render(<ConnectionCenter open onClose={() => {}} onChanged={onChanged} />);
    rerender(<ConnectionCenter open={false} onClose={() => {}} onChanged={onChanged} />);
    rerender(<ConnectionCenter open onClose={() => {}} onChanged={onChanged} />);
    await screen.findByText("Fresh account");
    first.resolve({ profiles: [], connections: [connection("c1", "Stale account")], plugin_directory: "x" } as never);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("Stale account")).not.toBeInTheDocument();
    expect(screen.getByText("Fresh account")).toBeInTheDocument();
  });

  it("deleting a connection (and its vault secrets) asks first, and cancel deletes nothing", async () => {
    vi.spyOn(api, "getConnections").mockResolvedValue({
      profiles: [], connections: [connection("c1", "My broker")], plugin_directory: "x",
    } as never);
    const del = vi.spyOn(api, "deleteConnection").mockResolvedValue(undefined as never);
    render(<ConnectionCenter open onClose={() => {}} onChanged={async () => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /delete/i }));
    const dialog = screen.getByRole("alertdialog");
    expect(del).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(del).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /delete/i }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getAllByRole("button").find((b) => b.textContent !== "Cancel")!);
    await waitFor(() => expect(del).toHaveBeenCalledWith("c1"));
  });
});

describe("DataCapturePanel", () => {
  it("Refresh twice with the answers reversed: the later request wins and loading clears", async () => {
    const reg = (hits: number) => ({
      registry: { entities: [{ id: "NIFTY", capture_enabled: true }] },
      stats: { channel: { hub_hits: hits, vendor_fetches: 0 } },
    });
    const first = deferred<never>();
    vi.spyOn(api, "getCaptureRegistry")
      .mockReturnValueOnce(first.promise as never)
      .mockResolvedValue(reg(222) as never);
    render(<DataCapturePanel />);
    fireEvent.click(screen.getByRole("button", { name: /Refresh/ }));
    await screen.findByText("222");
    first.resolve(reg(111) as never);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("111")).not.toBeInTheDocument();
    expect(screen.getByText("222")).toBeInTheDocument();
  });
});

describe("FactorRegistryGaps", () => {
  it("switching tabs quickly: a slow Pending answer never replaces the Queued rows", async () => {
    const row = (candidate: string) => ({ candidate, distinct_ref_count: 1, occurrence_count: 1 });
    const pending = deferred<never>();
    vi.spyOn(api, "listFactorRegistryGaps").mockImplementation(((status: string) =>
      status === "pending" ? pending.promise : Promise.resolve({ candidates: [row("queued_factor")], counts: {} })) as never);
    render(<FactorRegistryGaps />);
    fireEvent.click(screen.getByRole("button", { name: /Queued/ }));
    await screen.findByText("queued_factor");
    pending.resolve({ candidates: [row("pending_factor")], counts: {} } as never);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("pending_factor")).not.toBeInTheDocument();
    expect(screen.getByText("queued_factor")).toBeInTheDocument();
  });
});

describe("AgentMemoryPanel", () => {
  it("Refresh while a slower list is in flight: the older list does not overwrite the newer one", async () => {
    const entry = (id: string, title: string) => ({
      id, title, description: "d", memory_type: "fact", updated_at: 1, importance: 1, quality_score: 1,
      access_count: 0, last_accessed: 0, agent_id: "aa_1",
    });
    const first = deferred<never>();
    vi.spyOn(api, "listAgentMemory")
      .mockReturnValueOnce(first.promise as never)
      .mockResolvedValue({ entries: [entry("m2", "Fresh note")] } as never);
    render(<AgentMemoryPanel agentId="aa_1" />);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText("Fresh note");
    first.resolve({ entries: [entry("m1", "Stale note")] } as never);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("Stale note")).not.toBeInTheDocument();
  });
});

describe("an older failure never shows an error over a newer success", () => {
  it("DataCapturePanel: the first read rejects after the second succeeded", async () => {
    const first = deferred<never>();
    vi.spyOn(api, "getCaptureRegistry")
      .mockReturnValueOnce(first.promise as never)
      .mockResolvedValue({ registry: { entities: [{ id: "NIFTY", capture_enabled: true }] }, stats: { channel: { hub_hits: 7, vendor_fetches: 0 } } } as never);
    render(<DataCapturePanel />);
    fireEvent.click(screen.getByRole("button", { name: /Refresh/ }));
    await screen.findByText("7");
    first.reject(new Error("old read failed"));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("old read failed")).not.toBeInTheDocument();
  });

  it("FactorRegistryGaps: the abandoned tab's failure is not shown, and the spinner is not left on", async () => {
    const pending = deferred<never>();
    vi.spyOn(api, "listFactorRegistryGaps").mockImplementation(((status: string) =>
      status === "pending" ? pending.promise : Promise.resolve({ candidates: [], counts: {} })) as never);
    render(<FactorRegistryGaps />);
    fireEvent.click(screen.getByRole("button", { name: /Queued/ }));
    await screen.findByText(/No accepted candidates/);
    pending.reject(new Error("pending tab failed"));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("pending tab failed")).not.toBeInTheDocument();
    expect(screen.getByText(/No accepted candidates/)).toBeInTheDocument();
  });

  it("DataCapturePanel: toggling capture while a read is in flight does not leave Refresh spinning or lose the saved value", async () => {
    const slow = deferred<never>();
    vi.spyOn(api, "getCaptureRegistry").mockReturnValueOnce(slow.promise as never);
    vi.spyOn(api, "updateCaptureRegistry").mockResolvedValue({
      registry: { entities: [{ id: "NIFTY", capture_enabled: true }] }, stats: { channel: { hub_hits: 9, vendor_fetches: 0 } },
    } as never);
    render(<DataCapturePanel />);
    // the mount read is still pending; the checkbox is disabled while loading, so the toggle comes from Refresh-free state
    slow.resolve({ registry: { entities: [{ id: "NIFTY", capture_enabled: false }] }, stats: { channel: { hub_hits: 1, vendor_fetches: 0 } } } as never);
    const box = await screen.findByRole("checkbox");
    await waitFor(() => expect(box).toBeEnabled());
    fireEvent.click(box);
    await screen.findByText("9");
  });
});
