import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Compare } from "../Compare";
import type { RunData } from "@/lib/api";

const apiMock = vi.hoisted(() => ({
  listRuns: vi.fn(),
  getRun: vi.fn(),
}));
const toastMock = vi.hoisted(() => ({ error: vi.fn() }));

vi.mock("@/lib/api", () => ({ api: apiMock }));
vi.mock("sonner", () => ({ toast: toastMock }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const runs = [
  { run_id: "right", prompt: "Right", status: "success" },
  { run_id: "old", prompt: "Old", status: "success" },
  { run_id: "new", prompt: "New", status: "success" },
];

describe("Compare page", () => {
  beforeEach(() => {
    apiMock.listRuns.mockReset();
    apiMock.getRun.mockReset();
    toastMock.error.mockReset();
    apiMock.listRuns.mockResolvedValue(runs);
  });

  it("keeps the newest result when an older request resolves later", async () => {
    const oldRequest = deferred<RunData>();
    const newRequest = deferred<RunData>();
    apiMock.getRun.mockImplementation((runId: string) => {
      if (runId === "old") return oldRequest.promise;
      if (runId === "new") return newRequest.promise;
      return Promise.resolve({ status: "success", run_id: runId });
    });

    render(<Compare />);
    const selectors = await screen.findAllByRole("combobox");
    await waitFor(() => expect(selectors[0]).toHaveValue("old"));
    fireEvent.change(selectors[0], { target: { value: "new" } });

    await act(async () => {
      newRequest.resolve({ status: "success", run_id: "new", metrics: { total_return: 0.3 } });
      await newRequest.promise;
    });
    expect(await screen.findByText("30.00%")).toBeInTheDocument();

    await act(async () => {
      oldRequest.resolve({ status: "success", run_id: "old", metrics: { total_return: 0.1 } });
      await oldRequest.promise;
    });

    expect(screen.getByText("30.00%")).toBeInTheDocument();
    expect(screen.queryByText("10.00%")).not.toBeInTheDocument();
  });

  it("ignores an older error and keeps loading the current selection", async () => {
    const oldRequest = deferred<RunData>();
    const newRequest = deferred<RunData>();
    apiMock.getRun.mockImplementation((runId: string) => {
      if (runId === "old") return oldRequest.promise;
      if (runId === "new") return newRequest.promise;
      return Promise.resolve({ status: "success", run_id: runId });
    });

    const { container } = render(<Compare />);
    const selectors = await screen.findAllByRole("combobox");
    await waitFor(() => expect(selectors[0]).toHaveValue("old"));
    fireEvent.change(selectors[0], { target: { value: "new" } });

    await act(async () => {
      oldRequest.reject(new Error("old request failed"));
      await oldRequest.promise.catch(() => undefined);
    });

    expect(toastMock.error).not.toHaveBeenCalled();
    expect(container.querySelector(".animate-pulse")).toBeInTheDocument();
    expect(screen.queryByText("Select two runs to compare their metrics.")).not.toBeInTheDocument();

    await act(async () => {
      newRequest.resolve({ status: "success", run_id: "new", metrics: { total_return: 0.2 } });
      await newRequest.promise;
    });
    expect(await screen.findByText("20.00%")).toBeInTheDocument();
  });
  it("a metric the backend sent as null is a gap (em dash, no verdict), never a measured zero", async () => {
    apiMock.getRun.mockImplementation((runId: string) =>
      Promise.resolve({
        status: "success",
        run_id: runId,
        // the baseline run could not compute a Sharpe (null on the wire); the other run has a real one
        metrics: runId === "old" ? { sharpe: null, total_return: 0.1 } : { sharpe: 1.2, total_return: 0.2 },
      }),
    );
    render(<Compare />);
    const row = (await screen.findByText("Sharpe Ratio")).closest("tr") as HTMLElement;
    await waitFor(() => expect(row).toHaveTextContent("1.200"));
    expect(row).not.toHaveTextContent("0.000");
    expect(row).not.toHaveTextContent("+1.200");
    expect(row.textContent).not.toMatch(/[↑↓]/);
  });
  it("null on the compared side, a pct metric and the delta column are gaps too", async () => {
    apiMock.getRun.mockImplementation((runId: string) =>
      Promise.resolve({
        status: "success",
        run_id: runId,
        metrics: runId === "right" ? { total_return: null, max_drawdown: -0.1 } : { total_return: 0.3, max_drawdown: null },
      }),
    );
    render(<Compare />);
    const ret = (await screen.findByText("Total Return")).closest("tr") as HTMLElement;
    await waitFor(() => expect(ret).toHaveTextContent("30.00%"));
    expect(ret.textContent).not.toMatch(/(^|[^\d.])0\.00%/);
    expect(ret.textContent).not.toMatch(/[↑↓]/);
    const dd = screen.getByText("Max Drawdown").closest("tr") as HTMLElement;
    expect(dd.textContent).not.toMatch(/(^|[^\d.])0\.00%/);
    expect(dd.textContent).not.toMatch(/[↑↓]/);
  });
});
