import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAlphaJobStream } from "../useAlphaJobStream";

const toastError = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: toastError } }));

type Listener = (e: MessageEvent) => void;
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners = new Map<string, Listener[]>();
  closed = false;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(name: string, fn: Listener) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn]);
  }
  close() {
    this.closed = true;
  }
  emit(name: string, data?: unknown) {
    for (const fn of this.listeners.get(name) ?? []) fn({ data: data === undefined ? "" : JSON.stringify(data) } as MessageEvent);
  }
}

const messages = { unreadable: "unreadable result", streamError: "stream broke" };
const create = () => Promise.resolve({ job_id: "job-1" });
const streamUrl = () => Promise.resolve("/stream?ticket=t");

beforeEach(() => {
  FakeEventSource.instances = [];
  toastError.mockClear();
  vi.stubGlobal("EventSource", FakeEventSource);
});
afterEach(() => vi.unstubAllGlobals());

describe("useAlphaJobStream", () => {
  it("follows progress, result and done; the close after done is not an error", async () => {
    const { result } = renderHook(() => useAlphaJobStream<{ alive: number }>(messages));
    await act(async () => { await result.current.start(create, streamUrl); });
    const es = FakeEventSource.instances[0];
    expect(result.current.status).toBe("streaming");
    act(() => es.emit("progress", { n_done: 1, n_total: 4 }));
    expect(result.current.progress?.n_done).toBe(1);
    act(() => { es.emit("result", { alive: 3 }); es.emit("done"); es.emit("error"); });
    expect(result.current.result).toEqual({ alive: 3 });
    expect(result.current.status).toBe("done");
    expect(toastError).not.toHaveBeenCalled();
    expect(es.closed).toBe(true);
  });

  it("an unreadable result is a failed run", async () => {
    const { result } = renderHook(() => useAlphaJobStream(messages));
    await act(async () => { await result.current.start(create, streamUrl); });
    const es = FakeEventSource.instances[0];
    act(() => {
      for (const fn of es.listeners.get("result") ?? []) fn({ data: "{not json" } as MessageEvent);
      es.emit("done");
    });
    expect(result.current.status).toBe("error");
    expect(toastError).toHaveBeenCalledWith("unreadable result");
  });

  it("a stream error shows the server message and a create failure is rethrown", async () => {
    const { result } = renderHook(() => useAlphaJobStream(messages));
    await act(async () => { await result.current.start(create, streamUrl); });
    act(() => FakeEventSource.instances[0].emit("error", { message: "worker died" }));
    expect(toastError).toHaveBeenCalledWith("worker died");
    expect(result.current.status).toBe("error");

    await expect(
      act(async () => { await result.current.start(() => Promise.reject(new Error("single-asset")), streamUrl); }),
    ).rejects.toThrow("single-asset");
    expect(result.current.status).toBe("error");
  });

  it("does not open a stream when the view unmounts while the ticket is being minted", async () => {
    let release!: (url: string) => void;
    const slowUrl = () => new Promise<string>((resolve) => { release = resolve; });
    const { result, unmount } = renderHook(() => useAlphaJobStream(messages));
    let started!: Promise<void>;
    await act(async () => { started = result.current.start(create, slowUrl); });
    unmount();
    await act(async () => { release("/stream?ticket=late"); await started; });
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});
