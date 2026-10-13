import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { KnowledgeEngine } from "@/pages/KnowledgeEngine";
import { ApiError, api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, queryKnowledgeWiki: vi.fn(), getKnowledgeWikiPage: vi.fn() },
  };
});

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;
const entry = { slug: "concepts/kelly", type: "concept", title: "Kelly", tags: [], related: [], sources: [], summary: "s", score: 0.5 };

beforeEach(() => {
  vi.clearAllMocks();
  mocked.queryKnowledgeWiki.mockResolvedValue({ ok: true, count: 1, results: [entry] });
});

describe("KnowledgeEngine wiki tab", () => {
  it("a failed page load is shown and retried on the next open, never cached as the page", async () => {
    mocked.getKnowledgeWikiPage
      .mockRejectedValueOnce(new ApiError("wiki page unavailable", 502))
      .mockResolvedValueOnce({ ok: true, slug: entry.slug, found: true, content: "# Kelly body" });
    render(<KnowledgeEngine />);
    fireEvent.click(await screen.findByText("Kelly"));
    expect(await screen.findByText(/Failed to load: wiki page unavailable/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Kelly")); // close
    fireEvent.click(screen.getByText("Kelly")); // reopen -> retry
    expect(await screen.findByText("# Kelly body")).toBeInTheDocument();
    expect(mocked.getKnowledgeWikiPage).toHaveBeenCalledTimes(2);
  });

  it("a rejected slug (traversal) reads as not found, not as an empty page", async () => {
    mocked.getKnowledgeWikiPage.mockResolvedValue({ ok: true, slug: entry.slug, found: false });
    render(<KnowledgeEngine />);
    fireEvent.click(await screen.findByText("Kelly"));
    expect(await screen.findByText(/Page not found/)).toBeInTheDocument();
  });

  it("an entry without a score shows an em-dash instead of crashing the list", async () => {
    mocked.queryKnowledgeWiki.mockResolvedValue({ ok: true, count: 1, results: [{ ...entry, score: undefined }] });
    render(<KnowledgeEngine />);
    expect(await screen.findByText("Kelly")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
