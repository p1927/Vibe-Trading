import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { HubNewsPipelineStatus } from "@/lib/api";
import { NewsPipelineStatusStrip } from "../NewsPipelineStatusStrip";

afterEach(() => cleanup());

// Shape taken from a real release /trade/hub/status response (summary mode), trimmed.
const SUMMARY: HubNewsPipelineStatus = {
  ticker: "NIFTY",
  detail: "summary",
  omitted_blocks: ["llm_wiki.health", "llm_wiki.counts", "llm_wiki.search_probe"],
  distilled_event_count: 233,
  discarded_count: 1118,
  relevance_gate_enabled: true,
  llm_wiki: {
    health: { probed: false, reason: "detail=summary" },
    path_alignment: { aligned: true, expected_path: "/x/llm-wiki", registered_path: "/x/llm-wiki" },
    embedding_available: true,
    local_source_md_count: 437,
  },
};

describe("NewsPipelineStatusStrip", () => {
  it("renders the typed news_pipeline fields no other Hub panel shows", () => {
    render(<NewsPipelineStatusStrip status={SUMMARY} />);
    expect(screen.getByTestId("news-pipeline-status-wiki")).toHaveTextContent("not probed (summary)");
    expect(screen.getByTestId("news-pipeline-status-align")).toHaveTextContent("aligned");
    expect(screen.getByTestId("news-pipeline-status-embed")).toHaveTextContent("available");
    expect(screen.getByTestId("news-pipeline-status-sources")).toHaveTextContent("437");
    expect(screen.getByTestId("news-pipeline-status-events")).toHaveTextContent("233");
    expect(screen.getByTestId("news-pipeline-status-gate")).toHaveTextContent("on");
  });

  it("flags a misaligned wiki project path and shows both paths", () => {
    render(
      <NewsPipelineStatusStrip
        status={{
          ...SUMMARY,
          llm_wiki: {
            ...SUMMARY.llm_wiki,
            path_alignment: { aligned: false, expected_path: "/want", registered_path: "/got" },
          },
        }}
      />,
    );
    expect(screen.getByTestId("news-pipeline-status-align")).toHaveTextContent("MISALIGNED");
    expect(screen.getByTestId("news-pipeline-status-strip")).toHaveTextContent("registered /got ≠ expected /want");
  });

  it("reports a full-mode probe result as reachable/unreachable", () => {
    render(
      <NewsPipelineStatusStrip
        status={{ ...SUMMARY, detail: "full", llm_wiki: { ...SUMMARY.llm_wiki, health: { ok: false, reachable: false } } }}
      />,
    );
    expect(screen.getByTestId("news-pipeline-status-wiki")).toHaveTextContent("unreachable");
  });

  it("renders nothing without a payload", () => {
    const { container } = render(<NewsPipelineStatusStrip status={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});
