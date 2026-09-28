import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { ObservabilityIssuesPanel } from "../ObservabilityIssuesPanel";
import { api, type ObservabilityIssue } from "@/lib/api";

const issue: ObservabilityIssue = {
  issue_id: "watch:indmoney_token_expired:abc",
  severity: "error",
  module: "watch",
  event: "indmoney_token_expired",
  status: "open",
  first_seen: "2026-09-29T01:35:00Z",
  last_seen: "2026-09-29T01:35:00Z",
  count: 2,
  summary: "INDmoney token expired",
  detail: {},
  suggested_action: "Paste a fresh token",
};

describe("ObservabilityIssuesPanel (D365)", () => {
  it("lists open issues and resolves one", async () => {
    const list = vi.spyOn(api, "getObservabilityIssues");
    list.mockResolvedValueOnce({ issues: [issue], open_count: 1 }).mockResolvedValueOnce({ issues: [], open_count: 0 });
    const resolve = vi.spyOn(api, "resolveObservabilityIssue").mockResolvedValue({ issue_id: issue.issue_id, resolved: true });

    render(<ObservabilityIssuesPanel />);
    expect(await screen.findByText(/INDmoney token expired/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Resolve" }));

    await waitFor(() => expect(resolve).toHaveBeenCalledWith(issue.issue_id));
    await waitFor(() => expect(screen.queryByText(/INDmoney token expired/)).not.toBeInTheDocument());
  });
});
