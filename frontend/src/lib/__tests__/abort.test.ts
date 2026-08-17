import { describe, expect, it } from "vitest";
import { isAbortError } from "../abort";

describe("isAbortError", () => {
  it("returns true for a DOMException with name=AbortError", () => {
    const err = new DOMException("signal is aborted without reason", "AbortError");
    expect(isAbortError(err)).toBe(true);
  });

  it("returns false for a DOMException with a different name", () => {
    const err = new DOMException("boom", "NetworkError");
    expect(isAbortError(err)).toBe(false);
  });

  it("returns true for a plain object that duck-types { name: 'AbortError' }", () => {
    expect(isAbortError({ name: "AbortError", message: "x" })).toBe(true);
  });

  it("returns false for an Error with message 'signal is aborted without reason' but a different name", () => {
    // Some bundlers wrap the rejection as a plain Error before it surfaces
    // in unhandledrejection. The contract is on `name`, not the message —
    // a wrapped Error is NOT an abort.
    expect(isAbortError(new Error("signal is aborted without reason"))).toBe(false);
  });

  it("returns false for non-error values", () => {
    expect(isAbortError(null)).toBe(false);
    expect(isAbortError(undefined)).toBe(false);
    expect(isAbortError("AbortError")).toBe(false);
    expect(isAbortError(42)).toBe(false);
  });
});