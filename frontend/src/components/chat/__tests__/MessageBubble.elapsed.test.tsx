import { render } from "@testing-library/react";
import { MessageBubble } from "../MessageBubble";
import type { StoredAgentMessage } from "@/stores/agent";

vi.mock("react-markdown", () => ({
  default: ({ children }: { children: string }) => <div data-testid="markdown">{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));
vi.mock("rehype-highlight", () => ({ default: () => {} }));

const answer = (elapsed_ms: number): StoredAgentMessage =>
  ({ id: "m", type: "answer", content: "done", timestamp: 1, elapsed_ms }) as StoredAgentMessage;

describe("MessageBubble elapsed time", () => {
  it.each([Number.NaN, Number.POSITIVE_INFINITY, -50])("an unreadable elapsed_ms (%s) prints no time", (value) => {
    const { container } = render(<MessageBubble msg={answer(value)} />);
    expect(container.textContent).not.toMatch(/NaN|Infinity|-\d/);
    expect(container.textContent).not.toMatch(/\d+ ?(ms|s)\b/);
  });

  it("a real elapsed time still renders", () => {
    const { container } = render(<MessageBubble msg={answer(2500)} />);
    expect(container.textContent).toContain("2.5 s");
  });
});
