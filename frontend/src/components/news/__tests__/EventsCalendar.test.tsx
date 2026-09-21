import "@testing-library/jest-dom/vitest";
import { render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EventsCalendar } from "../EventsCalendar";

const apiMock = vi.hoisted(() => ({
  getHubNewsEventsCalendar: vi.fn(),
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
});
