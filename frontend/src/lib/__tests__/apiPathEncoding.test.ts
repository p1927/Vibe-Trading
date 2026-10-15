import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

// An id that reaches a URL path (a run id comes straight from the page URL) must be one encoded segment:
// "../settings/llm" in /runs/${id} is normalised by fetch into GET /settings/llm.
describe("api path segments are encoded", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("a hostile run id stays one path segment on every /runs and /sessions call", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("../api");
    const hostile = "../settings/llm?x=1#h";
    await Promise.all([
      api.getRun(hostile),
      api.getRunCode(hostile),
      api.getRunFactor(hostile),
      api.getRunPine(hostile),
      api.getRunAttribution(hostile),
      api.deleteSession(hostile),
      api.renameSession(hostile, "t"),
      api.autoTitleSession(hostile),
      api.sendMessage(hostile, "hi"),
      api.cancelSession(hostile),
      api.getSessionMessages(hostile),
      api.getSessionProvenance(hostile),
      api.getGoal(hostile),
      api.updateGoalStatus(hostile, {} as never),
    ]);
    const urls = fetchMock.mock.calls.map((call) => String((call as unknown[])[0]));
    expect(urls).toHaveLength(14);
    for (const url of urls) {
      expect(url).not.toContain("../");
      expect(url).toContain(encodeURIComponent(hostile));
    }
    expect(api.sseUrl(hostile)).toContain(encodeURIComponent(hostile));
  });

  it("api.ts interpolates no raw id into a path", () => {
    const text = readFileSync(join(__dirname, "../api.ts"), "utf8");
    const offenders = text
      .split("\n")
      .map((line, i) => ({ line, n: i + 1 }))
      .filter(({ line }) => /`[^`]*\/\$\{(?!encodeURIComponent|BASE|path\b|qs\b)[^}]+\}/.test(line))
      .map(({ line, n }) => `${n}: ${line.trim()}`);
    expect(offenders).toEqual([]);
  });
});
