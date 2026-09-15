import { describe, expect, it } from "vitest";
import { sourceNodes } from "../NewsPipelineGraph";

describe("sourceNodes", () => {
  it("builds one node per source family in the summary, most items first", () => {
    const nodes = sourceNodes({ total: 460, by_source: { searxng: 129, rss: 302, currents: 29 } });
    expect(nodes).toEqual([
      { key: "rss", label: "RSS", count: 302 },
      { key: "searxng", label: "SearXNG", count: 129 },
      { key: "currents", label: "Currents", count: 29 },
    ]);
  });

  it("shows an unknown family under its raw key and breaks count ties by key", () => {
    const nodes = sourceNodes({ by_source: { zeta_feed: 3, alpha_feed: 3 } });
    expect(nodes.map((n) => [n.key, n.label])).toEqual([
      ["alpha_feed", "alpha_feed"],
      ["zeta_feed", "zeta_feed"],
    ]);
  });

  it("has no source nodes for an empty or missing summary", () => {
    expect(sourceNodes(null)).toEqual([]);
    expect(sourceNodes({ total: 0 })).toEqual([]);
    expect(sourceNodes({ total: 0, by_source: {} })).toEqual([]);
  });
});
