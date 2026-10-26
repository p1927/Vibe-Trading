import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { GoalPanel } from "../GoalPanel";
import type { GoalSnapshot } from "@/lib/api";
import i18n from "@/i18n";

const apiMock = vi.hoisted(() => ({ updateGoalStatus: vi.fn() }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: apiMock };
});

const snapshot = {
  goal: { goal_id: "g1", objective: "Rank unusual volume", ui_summary: "" },
  claims: [],
  criteria: [],
  evidence: [],
  evidence_count: 0,
} as unknown as GoalSnapshot;

function open() {
  const onSnapshotChange = vi.fn();
  render(
    <GoalPanel
      snapshot={snapshot}
      sessionId="s1"
      streaming={false}
      openRequest={1}
      onSnapshotChange={onSnapshotChange}
      onContinue={vi.fn()}
    />,
  );
  return onSnapshotChange;
}

describe("GoalPanel cancel goal", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
    apiMock.updateGoalStatus.mockResolvedValue({});
  });
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("asks first and sends nothing when the prompt is dismissed", async () => {
    open();
    fireEvent.click(screen.getByRole("button", { name: "Cancel goal" }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(dialog.querySelectorAll("button")[0] as HTMLButtonElement);
    await act(async () => {});
    expect(apiMock.updateGoalStatus).not.toHaveBeenCalled();
  });

  it("two quick clicks open one prompt and a confirm cancels once", async () => {
    const onSnapshotChange = open();
    const cancel = screen.getByRole("button", { name: "Cancel goal" });
    fireEvent.click(cancel);
    fireEvent.click(cancel);
    const dialog = await screen.findByRole("alertdialog");
    expect(screen.getAllByRole("alertdialog")).toHaveLength(1);
    fireEvent.click(dialog.querySelectorAll("button")[1] as HTMLButtonElement);
    await waitFor(() => expect(apiMock.updateGoalStatus).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(onSnapshotChange).toHaveBeenCalledWith(null));
  });
});
