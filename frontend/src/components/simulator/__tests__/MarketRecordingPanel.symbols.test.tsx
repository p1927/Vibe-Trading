import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({
  getGlobalMacroUiCards: vi.fn(),
  getActiveMarketTickRecordings: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: apiMock };
});

import { MarketRecordingPanel } from "../MarketRecordingPanel";

describe("MarketRecordingPanel symbol list failure", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("keeps the 'Symbol list unavailable' error after a successful status poll", async () => {
    apiMock.getGlobalMacroUiCards.mockRejectedValue(new Error("boom"));
    apiMock.getActiveMarketTickRecordings.mockResolvedValue({ jobs: [] });
    render(<MarketRecordingPanel kind="fx" label="FX" />);
    await waitFor(() => expect(apiMock.getActiveMarketTickRecordings).toHaveBeenCalled());
    expect(await screen.findByTestId("market-recording-symbols-error")).toHaveTextContent(/Symbol list unavailable/);
  });
});
