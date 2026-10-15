import { render, screen } from "@testing-library/react";
import { ScheduledResearchProposalCard } from "../ScheduledResearchProposalCard";
import type { ScheduledResearchProposal } from "@/lib/api";

const proposal = (endAt: number | null): ScheduledResearchProposal => ({
  type: "scheduled_research.proposal",
  proposal_id: "p1",
  operation: "create",
  status: "pending",
  expires_at: 0,
  job: {
    id: "j1",
    title: "Nifty pre-open scan",
    state: "draft",
    source: { kind: "prompt", prompt: "scan" },
    schedule: { expression: "30 9 * * 1-5", timezone: "Asia/Kolkata", next_run_at: null, end_at: endAt },
    delivery: { channel: null, target_ref: null, target_label: null, status: "none" },
  },
});

describe("ScheduledResearchProposalCard", () => {
  it("shows the end time in IST with the zone label, whatever the browser zone is", () => {
    render(<ScheduledResearchProposalCard proposal={proposal(Date.UTC(2026, 9, 15, 4, 0, 0))} />);
    expect(screen.getByText("15 Oct 2026, 09:30 IST")).toBeInTheDocument();
  });

  it("an unreadable end time is a dash, never 'Invalid Date'", () => {
    render(<ScheduledResearchProposalCard proposal={proposal(Number.NaN)} />);
    expect(screen.queryByText(/Invalid Date/)).not.toBeInTheDocument();
  });
});
