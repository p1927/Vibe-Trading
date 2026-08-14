import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { SimulatorReplayCalendar, type ReplayRange } from "../SimulatorReplayCalendar";
import type { ReplayCalendarDay } from "@/lib/api";

function day(date: string, rows = 375): ReplayCalendarDay {
  return {
    date,
    has_nifty: true,
    has_banknifty: true,
    has_sensex: true,
    nifty_rows: rows,
    banknifty_rows: rows,
    sensex_rows: rows,
  };
}

// Most recent first, matching the /replay/calendar endpoint's ordering.
const DAYS: ReplayCalendarDay[] = [day("2024-04-17"), day("2024-04-16"), day("2024-04-15")];

describe("SimulatorReplayCalendar", () => {
  it("shows the empty state when there is no replay data", () => {
    render(
      <SimulatorReplayCalendar days={[]} range={null} armedRange={null} onRangeSelect={() => {}} />,
    );
    expect(screen.getByText(/No replay data on disk yet/i)).toBeInTheDocument();
  });

  it("first click anchors a single-day range", () => {
    const onRangeSelect = vi.fn();
    render(
      <SimulatorReplayCalendar
        days={DAYS}
        range={null}
        armedRange={null}
        onRangeSelect={onRangeSelect}
      />,
    );
    fireEvent.click(screen.getByTestId("replay-day-2024-04-15"));
    expect(onRangeSelect).toHaveBeenCalledWith({ start: "2024-04-15", end: "2024-04-15" });
  });

  it("second click completes the range in chronological order", () => {
    const onRangeSelect = vi.fn();
    const { rerender } = render(
      <SimulatorReplayCalendar
        days={DAYS}
        range={null}
        armedRange={null}
        onRangeSelect={onRangeSelect}
      />,
    );
    fireEvent.click(screen.getByTestId("replay-day-2024-04-15"));
    rerender(
      <SimulatorReplayCalendar
        days={DAYS}
        range={{ start: "2024-04-15", end: "2024-04-15" }}
        armedRange={null}
        onRangeSelect={onRangeSelect}
      />,
    );
    fireEvent.click(screen.getByTestId("replay-day-2024-04-17"));
    expect(onRangeSelect).toHaveBeenLastCalledWith({ start: "2024-04-15", end: "2024-04-17" });
  });

  it("clicking an earlier day second normalizes start/end order", () => {
    const onRangeSelect = vi.fn();
    const { rerender } = render(
      <SimulatorReplayCalendar
        days={DAYS}
        range={null}
        armedRange={null}
        onRangeSelect={onRangeSelect}
      />,
    );
    fireEvent.click(screen.getByTestId("replay-day-2024-04-17"));
    rerender(
      <SimulatorReplayCalendar
        days={DAYS}
        range={{ start: "2024-04-17", end: "2024-04-17" }}
        armedRange={null}
        onRangeSelect={onRangeSelect}
      />,
    );
    fireEvent.click(screen.getByTestId("replay-day-2024-04-15"));
    expect(onRangeSelect).toHaveBeenLastCalledWith({ start: "2024-04-15", end: "2024-04-17" });
  });

  it("a third click after a completed range starts a fresh single-day selection", () => {
    const onRangeSelect = vi.fn();
    const fullRange: ReplayRange = { start: "2024-04-15", end: "2024-04-17" };
    render(
      <SimulatorReplayCalendar
        days={DAYS}
        range={fullRange}
        armedRange={null}
        onRangeSelect={onRangeSelect}
      />,
    );
    fireEvent.click(screen.getByTestId("replay-day-2024-04-16"));
    expect(onRangeSelect).toHaveBeenLastCalledWith({ start: "2024-04-16", end: "2024-04-16" });
  });

  it("disables cells with no recorded data", () => {
    render(
      <SimulatorReplayCalendar days={DAYS} range={null} armedRange={null} onRangeSelect={() => {}} />,
    );
    const noDataCell = screen.getByTestId("replay-day-2024-04-14");
    expect(noDataCell).toBeDisabled();
  });
});
