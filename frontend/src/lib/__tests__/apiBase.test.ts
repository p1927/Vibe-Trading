import { afterEach, describe, expect, it, vi } from "vitest";

import { resolveApiBase } from "../apiBase";

function atPort(port: string) {
  vi.stubGlobal("window", {
    location: { protocol: "http:", hostname: "127.0.0.1", port },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("resolveApiBase", () => {
  it("points the dev UI at the dev API", () => {
    atPort("5899");
    expect(resolveApiBase()).toBe("http://127.0.0.1:8899");
  });

  it("points the release UI at the RELEASE API, not the dev one", () => {
    // The bug this pins: only the dev pair was mapped, so the release UI fell through to
    // same-origin. Vite's PROXY_PATHS does not list /board, so every board call then
    // resolved to the SPA's index.html — a 200 with text/html — and the whole Agent and
    // Advisory boards silently rendered empty instead of erroring.
    atPort("5909");
    expect(resolveApiBase()).toBe("http://127.0.0.1:8909");
  });

  it("keeps the two tiers separate", () => {
    atPort("5909");
    const release = resolveApiBase();
    atPort("5899");
    expect(resolveApiBase()).not.toBe(release);
  });

  it("stays same-origin when the API is served from the same port", () => {
    atPort("8899");
    expect(resolveApiBase()).toBe("");
  });
});
