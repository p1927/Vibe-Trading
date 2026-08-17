import "@testing-library/jest-dom/vitest";
import { describe, expect, it } from "vitest";
import { render, within } from "@testing-library/react";
import {
  RECORDING_FIELD_GROUPS,
  SimulatorRecordingFields,
} from "../SimulatorRecordingFields";

describe("SimulatorRecordingFields", () => {
  it("renders a disclosure with the recorder-relevant surface groups", () => {
    const { container } = render(<SimulatorRecordingFields />);

    // The <details> wrapper carries the testid; the disclosure content is
    // hidden by default in jsdom (no real layout), so we read it via
    // querySelector to assert the full group set renders.
    const details = container.querySelector(
      '[data-testid="simulator-recording-fields"]',
    );
    expect(details).not.toBeNull();
    const detailsScope = details as HTMLElement;
    expect(
      within(detailsScope).getByText(/What we record \(INDmoney API surface\)/i),
    ).toBeInTheDocument();
    for (const group of RECORDING_FIELD_GROUPS) {
      expect(within(detailsScope).getByText(group.title)).toBeInTheDocument();
    }
  });

  it("lists every INDmoney market-data endpoint the recorder actually calls", () => {
    // Anchored to the adapter at openalgo/broker/indmoney/api/. New market-data
    // endpoints added there should also appear here so users see the full surface.
    // Orders/portfolio/account endpoints are intentionally out of scope — the
    // disclosure only covers what the day recorder captures.
    const expectedEndpoints = [
      "/market/option-chain",
      "/market/quotes/mkt",
      "/market/quotes/full",
      "/market/quotes/ltp",
      "/market/quotes",
      "/market/instruments",
      "/market/historical",
    ];
    const { container } = render(<SimulatorRecordingFields />);
    const codes = Array.from(container.querySelectorAll("code"));
    const codeText = codes.map((c) => c.textContent ?? "").join("\n");
    for (const endpoint of expectedEndpoints) {
      expect(codeText, `endpoint not surfaced: ${endpoint}`).toContain(endpoint);
    }
  });

  it("distinguishes recorded vs wired vs available items", () => {
    const { container } = render(<SimulatorRecordingFields />);

    // Each status appears at least once (legend + body).
    const recorded = Array.from(container.querySelectorAll("span")).filter(
      (s) => (s.textContent ?? "").trim() === "Recorded today",
    );
    const wired = Array.from(container.querySelectorAll("span")).filter(
      (s) =>
        (s.textContent ?? "").trim() ===
        "Wired (used by OpenAlgo, not the recorder)",
    );
    const available = Array.from(container.querySelectorAll("span")).filter(
      (s) => (s.textContent ?? "").trim() === "Available — not yet wired",
    );

    // Legend contributes one of each; the body adds more for "Recorded today"
    // (the recorder's five fields) and "Available" (order-management,
    // historical, ltp). "Wired" lives only in the legend because no
    // recorder-only path consumes the openalgo adapter.
    expect(recorded.length).toBeGreaterThanOrEqual(2);
    expect(wired.length).toBeGreaterThanOrEqual(1);
    expect(available.length).toBeGreaterThanOrEqual(2);
  });

  it("shows cadence labels and explains per-category controls", () => {
    const { container } = render(<SimulatorRecordingFields />);
    const allText = container.textContent ?? "";
    expect(allText).toMatch(/Per tick \(WebSocket\)/);
    expect(allText).toMatch(/Per poll cycle \(configurable: 10s default, 1m supported\)/);
    expect(allText).toMatch(/Once per session \(instruments master\)/);
    expect(allText).toMatch(/On session end \/ export/);
    expect(allText).toMatch(/On demand \(triggered\)/);
    // The legend callout points at the per-category / per-WS dropdowns.
    expect(allText).toMatch(/Each REST poll has its own cadence/);
  });

  it("renders a <select> per REST category and the WS row", () => {
    const { container } = render(<SimulatorRecordingFields />);
    // Default config (no provider wrapping) is no-op, but selects still render.
    const optionChain = container.querySelector(
      '[data-testid="interval-select-option_chain"]',
    );
    const marketDepth = container.querySelector(
      '[data-testid="interval-select-market_depth"]',
    );
    const fullQuote = container.querySelector(
      '[data-testid="interval-select-full_quote"]',
    );
    const equityChain = container.querySelector(
      '[data-testid="interval-select-equity_option_chain"]',
    );
    const equityDepth = container.querySelector(
      '[data-testid="interval-select-equity_market_depth"]',
    );
    const equityQuote = container.querySelector(
      '[data-testid="interval-select-equity_full_quote"]',
    );
    const wsThrottle = container.querySelector('[data-testid="ws-throttle-select"]');
    expect(optionChain).not.toBeNull();
    expect(marketDepth).not.toBeNull();
    expect(fullQuote).not.toBeNull();
    expect(equityChain).not.toBeNull();
    expect(equityDepth).not.toBeNull();
    expect(equityQuote).not.toBeNull();
    expect(wsThrottle).not.toBeNull();
    // Each REST select has 18 options (off + 17 intervals).
    expect(optionChain?.querySelectorAll("option")).toHaveLength(18);
    // The WS select has 5 options (unlimited + 4 hz tiers).
    expect(wsThrottle?.querySelectorAll("option")).toHaveLength(5);
  });

  it("renders a historical-candle row with interval + lookback selects", () => {
    const { container } = render(<SimulatorRecordingFields />);
    const intervalSel = container.querySelector(
      '[data-testid="historical-interval-select"]',
    );
    const lookbackSel = container.querySelector(
      '[data-testid="historical-lookback-select"]',
    );
    expect(intervalSel).not.toBeNull();
    expect(lookbackSel).not.toBeNull();
    // Per the plan: 10 intervals × 8 lookbacks.
    expect(intervalSel?.querySelectorAll("option")).toHaveLength(10);
    expect(lookbackSel?.querySelectorAll("option")).toHaveLength(8);
  });

  it("renders no control on rows without controlKind (read-only)", () => {
    const { container } = render(<SimulatorRecordingFields />);
    // 6 REST polls + 1 WS + 2 historical = 9 selects total.
    // The non-controllable rows (Bulk quotes, Holdings, Positions, etc.)
    // render no select at all.
    const selects = container.querySelectorAll("select");
    expect(selects).toHaveLength(9);
  });

  it("propagates disabled to the per-row dropdowns", () => {
    const { container } = render(<SimulatorRecordingFields disabled={true} />);
    const selects = container.querySelectorAll("select");
    expect(selects.length).toBeGreaterThan(0);
    for (const sel of Array.from(selects)) {
      expect((sel as HTMLSelectElement).disabled).toBe(true);
    }
  });

  it("exposes a stable, exported field inventory for future automation", () => {
    // The inventory is the single source of truth — the page consumes it
    // through RECORDING_FIELD_GROUPS so future endpoints stay in lock-step
    // with the rendered disclosure. Lock its shape here.
    const allFields = RECORDING_FIELD_GROUPS.flatMap((g) =>
      g.sections.flatMap((s) => s.fields),
    );
    expect(allFields.length).toBeGreaterThan(10);
    for (const field of allFields) {
      expect(field.label.length).toBeGreaterThan(0);
      expect(field.fields.length).toBeGreaterThan(0);
      expect(["per-tick", "per-poll", "per-request", "session-end", "on-demand"])
        .toContain(field.cadence);
      expect(["recorded", "wired", "available"]).toContain(field.status);
    }
  });
});
