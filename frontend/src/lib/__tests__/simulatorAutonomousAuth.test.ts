// Guard for the simulator / autonomous-agent pages: the backend's mutating routes need
// local-or-token auth, and the Bearer header is only sent by lib/api.ts's request(). A page that
// called fetch() or opened an EventSource itself would silently lose it (401 on a remote stack).
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { setApiAuthKey } from "../apiAuth";

const SRC = join(__dirname, "..", "..");
const SCOPE = [
  "pages/Simulator.tsx",
  "pages/Autonomous.tsx",
  "pages/AgentBoard.tsx",
  "pages/AdvisoryBoard.tsx",
  "pages/PositionsBoard.tsx",
  "pages/ExecutionAdvisor.tsx",
  "components/simulator",
  "components/autonomous",
  "components/board",
  "components/options",
];

function sourceFiles(rel: string): string[] {
  const p = join(SRC, rel);
  if (statSync(p).isFile()) return [p];
  return readdirSync(p).flatMap((name) => {
    const child = join(rel, name);
    if (name === "__tests__") return [];
    return statSync(join(SRC, child)).isDirectory() ? sourceFiles(child) : /\.tsx?$/.test(name) ? [join(SRC, child)] : [];
  });
}

describe("simulator/autonomous pages reach the backend only through lib/api.ts", () => {
  it("no bare fetch( or new EventSource( outside lib/api.ts and hooks/useSSE", () => {
    const offenders = SCOPE.flatMap(sourceFiles).filter((f) =>
      /\bfetch\(|new EventSource\(|new WebSocket\(|XMLHttpRequest/.test(readFileSync(f, "utf8")),
    );
    expect(offenders).toEqual([]);
  });
});

describe("mutating routes send the Bearer key", () => {
  beforeEach(() => {
    localStorage.clear();
    setApiAuthKey("tok-123");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  const calls: Array<[string, () => Promise<unknown>]> = [
    ["clear-all", () => api.clearAllAutonomousAgents()],
    ["delete agent", () => api.deleteAutonomousAgent("aa_1", { flattenPositions: true })],
    ["pause", () => api.pauseAutonomousAgent("aa_1")],
    ["resume", () => api.resumeAutonomousAgent("aa_1")],
    ["commit", () => api.commitAutonomousAgent({ proposal_id: "p", consent_ack: true })],
    ["next simulation", () => api.answerNextSimulation("aa_1", { configuration: "decline" })],
    ["apply weight proposal", () => api.applyWeightProposal("w1")],
    ["revert weight proposal", () => api.revertWeightProposal("w1", "why")],
    ["apply retrain proposal", () => api.applyRetrainProposal("r1")],
    ["arm multi-market replay", () => api.armMultiMarketReplay({ markets: ["US"], speed: 1 })],
    ["set replay speed", () => api.setReplaySpeed(2)],
    ["stop replay", () => api.stopReplay()],
    ["stop recording", () => api.stopRecording("job1")],
  ];

  it.each(calls)("%s", async (_name, call) => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response("{}", { status: 200, headers: { "content-type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchSpy);
    await call();
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const init = fetchSpy.mock.calls[0][1] as RequestInit;
    expect(["POST", "DELETE", "PUT"]).toContain(init.method);
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-123");
  });
});
