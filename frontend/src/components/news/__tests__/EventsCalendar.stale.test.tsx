import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EventsCalendar } from "../EventsCalendar";
import { EventDetailPanel } from "../EventDetailPanel";

const apiMock = vi.hoisted(() => ({
  getHubNewsEventsCalendar: vi.fn(),
  getIndexPredictionUpcomingEventsHistory: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));

afterEach(() => cleanup());

describe("EventsCalendar stale week", () => {
  it("events of the week that was shown are not kept when the next week fails to load", async () => {
    apiMock.getHubNewsEventsCalendar.mockReset();
    apiMock.getHubNewsEventsCalendar
      .mockResolvedValueOnce({ events: [{ event: "Undated old-week event", articles: [] }] })
      .mockRejectedValueOnce(new Error("calendar 500"));
    render(<EventsCalendar />);
    expect(await screen.findByText("Undated old-week event")).toBeInTheDocument();
    for (let i = 0; i < 7; i += 1) fireEvent.click(screen.getByLabelText("Next day"));
    expect(await screen.findByRole("alert")).toHaveTextContent("calendar 500");
    expect(screen.queryByText("Undated old-week event")).toBeNull();
  });
});

describe("today is the IST calendar day, whatever the browser zone is", () => {
  const originalTz = process.env.TZ;
  beforeEach(() => {
    process.env.TZ = "America/Los_Angeles";
    vi.useFakeTimers({ toFake: ["Date"] });
    // 20:00 UTC on 15 Oct is 01:30 IST on 16 Oct but 13:00 on 15 Oct in Los Angeles.
    vi.setSystemTime(new Date("2026-10-15T20:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
    if (originalTz === undefined) delete process.env.TZ;
    else process.env.TZ = originalTz;
  });

  it("the calendar opens on the IST day", async () => {
    apiMock.getHubNewsEventsCalendar.mockReset();
    apiMock.getHubNewsEventsCalendar.mockResolvedValue({ events: [] });
    render(<EventsCalendar />);
    expect(await screen.findByText(/Fri, October 16 2026/)).toBeInTheDocument();
  });

  it("an event dated the previous IST day shows its Result section", () => {
    render(
      <EventDetailPanel
        item={{ kind: "news", key: "k", data: { event: "RBI policy", date: "2026-10-15", articles: [] } as never }}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("Result")).toBeInTheDocument();
  });

  it("an event dated the current IST day is not yet a past event", () => {
    render(
      <EventDetailPanel
        item={{ kind: "news", key: "k", data: { event: "RBI policy", date: "2026-10-16", articles: [] } as never }}
        onClose={() => {}}
      />,
    );
    expect(screen.queryByText("Result")).toBeNull();
  });
});
