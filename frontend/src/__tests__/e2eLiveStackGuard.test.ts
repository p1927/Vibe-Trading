import { afterEach, describe, expect, it, vi } from "vitest";

async function load(env: Record<string, string>) {
  vi.resetModules();
  for (const [k, v] of Object.entries(env)) vi.stubEnv(k, v);
  return import("../../e2e/helpers/liveStack");
}

afterEach(() => vi.unstubAllEnvs());

describe("e2e live-stack write guard", () => {
  it("reads always pass; writes to live dev ports are refused by default", async () => {
    const g = await load({ E2E_ALLOW_LIVE_WRITES: "", E2E_STACK_PAPER: "" });
    expect(g.blockedReason("GET", "http://127.0.0.1:8899/autonomous-agents")).toBeNull();
    expect(g.blockedReason("POST", "http://127.0.0.1:8899/autonomous-agents/a1/pause")).toMatch(/refused/);
    expect(g.blockedReason("post", "http://localhost:8902/control/replay/speed")).toMatch(/refused/);
    expect(g.blockedReason("DELETE", "http://localhost:5001/api/v1/x")).toMatch(/refused/);
  });

  it("needs BOTH opt-ins, and release ports are never writable", async () => {
    let g = await load({ E2E_ALLOW_LIVE_WRITES: "1", E2E_STACK_PAPER: "" });
    expect(g.blockedReason("POST", "http://127.0.0.1:8899/x")).toMatch(/refused/);
    g = await load({ E2E_ALLOW_LIVE_WRITES: "1", E2E_STACK_PAPER: "1" });
    expect(g.blockedReason("POST", "http://127.0.0.1:8899/x")).toBeNull();
    expect(g.blockedReason("POST", "http://127.0.0.1:8909/x")).toMatch(/release/);
    expect(g.blockedReason("PUT", "http://127.0.0.1:5011/x")).toMatch(/release/);
  });

  it("chat sessions stay open; other origins and bad urls are not this guard's business", async () => {
    const g = await load({ E2E_ALLOW_LIVE_WRITES: "", E2E_STACK_PAPER: "" });
    expect(g.blockedReason("POST", "http://127.0.0.1:8899/sessions")).toBeNull();
    expect(g.blockedReason("POST", "http://127.0.0.1:8899/sessions/s1/messages")).toBeNull();
    expect(g.blockedReason("POST", "http://127.0.0.1:8899/sessionsX")).toMatch(/refused/);
    expect(g.blockedReason("POST", "https://example.com/x")).toBeNull();
    expect(g.blockedReason("POST", "not a url")).toBeNull();
  });
});
