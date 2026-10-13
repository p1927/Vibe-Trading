import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { RepeatIntervalPicker, withValue } from "../RepeatIntervalPicker";

afterEach(() => cleanup());

describe("RepeatIntervalPicker", () => {
  it("withValue keeps the list and adds a missing current value in order", () => {
    expect(withValue([5, 10], 10)).toEqual([5, 10]);
    expect(withValue([5, 10, 45], 20)).toEqual([5, 10, 20, 45]);
  });

  it("shows a stored */20 and minute 7 as they are, not as the first option", () => {
    const { rerender } = render(<RepeatIntervalPicker value="*/20 * * * *" onChange={() => {}} />);
    expect(screen.getByDisplayValue("every 20 min")).toBeInTheDocument();
    rerender(<RepeatIntervalPicker value="7 9 * * *" onChange={() => {}} />);
    expect(screen.getByDisplayValue("07m")).toBeInTheDocument();
    expect(screen.getByDisplayValue("09h")).toBeInTheDocument();
  });
});
