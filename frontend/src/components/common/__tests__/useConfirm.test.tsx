import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useConfirm } from "../useConfirm";

function Harness({ onAnswer }: { onAnswer: (ok: boolean) => void }) {
  const [confirm, dialog] = useConfirm();
  return (
    <>
      <button type="button" onClick={() => void confirm({ title: "First?", confirmLabel: "Yes first" }).then(onAnswer)}>
        ask first
      </button>
      <button type="button" onClick={() => void confirm({ title: "Second?", confirmLabel: "Yes second" }).then(onAnswer)}>
        ask second
      </button>
      {dialog}
    </>
  );
}

describe("useConfirm", () => {
  it("resolves true on confirm and false on cancel, through the one ConfirmDialog", async () => {
    const answers: boolean[] = [];
    render(<Harness onAnswer={(ok) => answers.push(ok)} />);
    fireEvent.click(screen.getByText("ask first"));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("First?");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(answers).toEqual([false]));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("ask first"));
    fireEvent.click(screen.getByRole("button", { name: "Yes first" }));
    await waitFor(() => expect(answers).toEqual([false, true]));
  });

  it("Escape declines", async () => {
    const answers: boolean[] = [];
    render(<Harness onAnswer={(ok) => answers.push(ok)} />);
    fireEvent.click(screen.getByText("ask first"));
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(answers).toEqual([false]));
  });

  it("a second ask cancels the first, so one click can never answer both", async () => {
    const answers: boolean[] = [];
    render(<Harness onAnswer={(ok) => answers.push(ok)} />);
    fireEvent.click(screen.getByText("ask first"));
    fireEvent.click(screen.getByText("ask second"));
    await waitFor(() => expect(answers).toEqual([false]));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("Second?");
    fireEvent.click(screen.getByRole("button", { name: "Yes second" }));
    await waitFor(() => expect(answers).toEqual([false, true]));
  });

  it("an unmounted page does not leave a handler awaiting forever", async () => {
    const answers: boolean[] = [];
    const { unmount } = render(<Harness onAnswer={(ok) => answers.push(ok)} />);
    fireEvent.click(screen.getByText("ask first"));
    unmount();
    await waitFor(() => expect(answers).toEqual([false]));
  });
});

// ONE confirm style (D306): the browser's native window.confirm is not used anywhere in src.
describe("no window.confirm in src", () => {
  const root = join(__dirname, "../../..");
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) {
        if (name !== "__tests__" && name !== "tests") walk(full);
      } else if (/\.tsx?$/.test(name)) files.push(full);
    }
  };
  walk(root);

  it("every confirmation goes through ConfirmDialog / useConfirm", () => {
    const offenders: string[] = [];
    for (const file of files) {
      const text = readFileSync(file, "utf8");
      for (const m of text.matchAll(/\bwindow\.confirm\(|(?<![.\w])confirm\(\s*[`"']/g)) {
        offenders.push(`${file.slice(root.length)}: ${text.slice(m.index!, m.index! + 50).split("\n")[0]}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
