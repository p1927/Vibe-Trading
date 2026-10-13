import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EventsCalendar } from "../EventsCalendar";

const apiMock = vi.hoisted(() => ({
  getHubNewsEventsCalendar: vi.fn(),
  getIndexPredictionUpcomingEventsHistory: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));

describe("EventsCalendar", () => {
  it("reloads news-extracted events when its SSE-backed refresh key changes", async () => {
    apiMock.getHubNewsEventsCalendar.mockResolvedValue({ events: [] });
    const { rerender } = render(<EventsCalendar refreshKey="first-news-snapshot" />);

    await waitFor(() => expect(apiMock.getHubNewsEventsCalendar).toHaveBeenCalledTimes(1));
    rerender(<EventsCalendar refreshKey="new-news-snapshot" />);
    await waitFor(() => expect(apiMock.getHubNewsEventsCalendar).toHaveBeenCalledTimes(2));
  });

  it("moving the selected day inside the shown week does not refetch the week", async () => {
    apiMock.getHubNewsEventsCalendar.mockClear();
    apiMock.getHubNewsEventsCalendar.mockResolvedValue({ events: [] });
    render(<EventsCalendar />);
    await waitFor(() => expect(apiMock.getHubNewsEventsCalendar).toHaveBeenCalledTimes(1));
    // the selected day is today; stepping to a day of the same week keeps the same window
    const today = new Date();
    const step = today.getDay() === 6 ? -1 : 1;
    fireEvent.click(screen.getByLabelText(step === 1 ? "Next day" : "Previous day"));
    await new Promise((r) => setTimeout(r, 30));
    expect(apiMock.getHubNewsEventsCalendar).toHaveBeenCalledTimes(1);
  });

  it("says so when the past-structural-events source fails instead of reading as 'no past events'", async () => {
    apiMock.getHubNewsEventsCalendar.mockResolvedValue({ events: [] });
    apiMock.getIndexPredictionUpcomingEventsHistory.mockRejectedValue(new Error("history 502"));
    render(<EventsCalendar includeStructuralHistory />);
    expect(await screen.findByText(/Past structural events could not be loaded.*history 502/)).toBeInTheDocument();
  });
});
