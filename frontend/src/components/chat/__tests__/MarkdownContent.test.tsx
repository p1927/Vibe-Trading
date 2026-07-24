import { render, screen } from "@testing-library/react";
import { MarkdownContent } from "../MarkdownContent";

vi.mock("@/stores/provenance", () => ({
  preprocessCitationLinks: (content: string) => content,
}));

describe("MarkdownContent", () => {
  it("renders partial markdown with structured elements during stream", () => {
    render(<MarkdownContent content={"## Heading\n\n- first item"} />);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Heading");
    expect(screen.getByRole("listitem")).toHaveTextContent("first item");
  });

  it("renders streaming cursor when showCursor is true", () => {
    const { container } = render(<MarkdownContent content="Streaming..." showCursor />);
    expect(container.querySelector(".animate-pulse")).not.toBeNull();
  });

  it("does not render cursor by default", () => {
    const { container } = render(<MarkdownContent content="Done." />);
    expect(container.querySelector(".animate-pulse")).toBeNull();
  });
});
