import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useThrottledValue } from "../useThrottledValue";

describe("useThrottledValue", () => {
  beforeEach(() => {
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      cb(0);
      return 1;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the initial value immediately", () => {
    const { result } = renderHook(() => useThrottledValue("hello"));
    expect(result.current).toBe("hello");
  });

  it("coalesces rapid updates to the latest value on the next frame", () => {
    const { result, rerender } = renderHook(
      ({ value }) => useThrottledValue(value),
      { initialProps: { value: "a" } },
    );

    rerender({ value: "ab" });
    rerender({ value: "abc" });

    expect(result.current).toBe("abc");
  });

  it("flushes synchronously when value clears to empty string", () => {
    const { result, rerender } = renderHook(
      ({ value }) => useThrottledValue(value),
      { initialProps: { value: "partial" } },
    );

    rerender({ value: "" });

    expect(result.current).toBe("");
  });
});
