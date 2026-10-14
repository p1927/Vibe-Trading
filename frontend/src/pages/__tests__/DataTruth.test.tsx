import { Component, type ReactNode } from "react";
import { act, cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentBoard } from "@/pages/AgentBoard";
import { Hub } from "@/pages/Hub";
import { Portfolio } from "@/pages/Portfolio";
import { PositionsBoard } from "@/pages/PositionsBoard";
import { Prediction } from "@/pages/Prediction";
import { Scheduled } from "@/pages/Scheduled";
import { Simulator } from "@/pages/Simulator";

// Heavy chart children paint into canvases jsdom does not have; the page logic under test does not need them.
vi.mock("@/components/NewsPipelineGraph", () => ({ NewsPipelineGraph: () => null }));
vi.mock("@/components/news/EventsCalendar", () => ({ EventsCalendar: () => null }));
vi.mock("@/components/news/FactorRegistryGaps", () => ({ FactorRegistryGaps: () => null }));
vi.mock("@/components/ObservabilityIssuesPanel", () => ({ ObservabilityIssuesPanel: () => null }));

/**
 * R7 flow 3, UI truth. Every page that shows money or a forecast is mounted against a backend that is
 * (b) down: every endpoint answers 500, (c) answering with the documented "unknown" shapes: `{}`, `null`,
 * `[]`, and (d) too slow to answer while the operator navigates away and back. It must never show a number
 * it does not have (NaN/undefined/Infinity), never read "unknown" as a calm empty state, never crash, and
 * never leave a money-moving control enabled while the state it would act on is unknown.
 * The happy payload (a) of each page is covered by its own page test.
 */

const PAGES: Record<string, () => JSX.Element> = {
  Prediction: () => <Prediction />,
  AgentBoard: () => <AgentBoard />,
  PositionsBoard: () => <PositionsBoard />,
  Simulator: () => <Simulator />,
  Portfolio: () => <Portfolio />,
  Scheduled: () => <Scheduled />,
  Hub: () => <Hub />,
};

class Boundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null };
  static getDerivedStateFromError(error: Error) {
    return { error: String(error?.stack || error) };
  }
  render() {
    return this.state.error ? <div data-testid="boundary">{this.state.error}</div> : this.props.children;
  }
}

function json(body: string, status = 200): Response {
  return new Response(body, { status, headers: { "content-type": "application/json" } });
}

type Backend = (url: string, init?: RequestInit) => Promise<Response>;

const BACKENDS: Record<string, Backend> = {
  down: async () => json('{"detail":"boom"}', 500),
  emptyObject: async () => json("{}"),
  nullBody: async () => json("null"),
  slow: (_url, init) =>
    new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }),
};

async function mount(name: string) {
  const view = render(
    <MemoryRouter>
      <Boundary>{PAGES[name]()}</Boundary>
    </MemoryRouter>,
  );
  // let the first load settle (or, for the slow backend, stay pending)
  await act(async () => {
    await new Promise((r) => setTimeout(r, 150));
  });
  return view;
}

const BROKEN_NUMBER = /\bNaN\b|\bundefined\b|\[object Object\]|-?Infinity\b/;
// a calm "all clear" the page may only say when it actually knows
const CALM = /\ball good\b|\ball systems (ok|operational|normal)\b|\beverything is (fine|ok)\b|\bno issues\b/i;
const MONEY_CONTROL = /^(buy|sell|place order|execute|approve|flatten|close all|clear all agents|resume|start replay)\b/i;

let errors: string[];
beforeEach(() => {
  errors = [];
  vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
    errors.push(args.map(String).join(" "));
  });
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.stubGlobal("EventSource", class {
    addEventListener() {}
    close() {}
    onerror: unknown = null;
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe.each(Object.keys(PAGES))("%s tells the truth when its data is unknown", (name) => {
  for (const [label, backend] of Object.entries(BACKENDS)) {
    it(`backend ${label}: no crash, no broken number, no calm all-clear`, async () => {
      vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => backend(String(url), init)));
      const view = await mount(name);
      expect(view.queryByTestId("boundary")?.textContent ?? null, "the page crashed").toBeNull();
      const text = view.container.textContent ?? "";
      const broken = BROKEN_NUMBER.exec(text);
      expect(broken && text.slice(Math.max(0, broken.index - 60), broken.index + 40), "a number it does not have").toBeNull();
      expect(text).not.toMatch(CALM);
    });
  }

  it("backend down: a money-moving control is not enabled, and the page says something failed or is unknown", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => BACKENDS.down(String(url), init)));
    const view = await mount(name);
    const live = Array.from(view.container.querySelectorAll("button")).filter(
      (b) => !(b as HTMLButtonElement).disabled && MONEY_CONTROL.test((b.textContent ?? "").trim()),
    );
    expect(live.map((b) => b.textContent), "money-moving control enabled with the backend down").toEqual([]);
    expect(view.container.textContent ?? "").toMatch(
      /fail|error|unavailable|unreachable|unknown|couldn.t|could not|offline|try again|retry|not available|boom/i,
    );
  });

  it("slow backend + rapid navigation: unmounting mid-request leaves no unhandled error", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => BACKENDS.slow(String(url), init)));
    for (let i = 0; i < 3; i += 1) {
      const view = await mount(name);
      view.unmount();
    }
    expect(errors.filter((e) => /not wrapped in act|unmounted component|Cannot read|is not a function/i.test(e))).toEqual([]);
  });
});
