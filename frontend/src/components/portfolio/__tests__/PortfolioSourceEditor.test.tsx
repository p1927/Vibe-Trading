import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { PortfolioSourceEditor } from "../PortfolioSourceEditor";
import type { PortfolioSettings } from "@/lib/api";
import i18n from "@/i18n";

vi.mock("@/components/portfolio/ConnectionCenter", () => ({ ConnectionCenter: () => null }));

const src = (id: string, label: string) => ({ connection_id: id, label, enabled: true, order: 0, include_cash: true });
const a: PortfolioSettings = { display_currency: "USD", sources: [src("c1", "Alpha")] };
const refreshed: PortfolioSettings = { display_currency: "USD", sources: [src("c1", "Alpha")] };

function ui(settings: PortfolioSettings, onClose = vi.fn()) {
  return (
    <PortfolioSourceEditor
      open
      settings={settings}
      catalog={[]}
      saving={false}
      onClose={onClose}
      onSave={vi.fn()}
      onConnectionsChanged={vi.fn()}
    />
  );
}

describe("PortfolioSourceEditor unsaved edits", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
  });
  afterEach(cleanup);

  it("a parent refresh with a new settings object does not wipe an in-progress edit", () => {
    const { rerender } = render(ui(a));
    const label = screen.getByLabelText("Account label") as HTMLInputElement;
    fireEvent.change(label, { target: { value: "Renamed" } });
    rerender(ui(refreshed));
    expect((screen.getByLabelText("Account label") as HTMLInputElement).value).toBe("Renamed");
  });

  it("a backdrop click asks before discarding an edit, and closes at once when nothing changed", async () => {
    const onClose = vi.fn();
    const { container, rerender } = render(ui(a, onClose));
    const backdrop = container.firstElementChild as HTMLElement;
    fireEvent.mouseDown(backdrop);
    await act(async () => {});
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByLabelText("Account label"), { target: { value: "Renamed" } });
    rerender(ui(a, onClose));
    fireEvent.mouseDown(backdrop);
    const dialog = await screen.findByRole("alertdialog");
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(dialog.querySelectorAll("button")[0] as HTMLButtonElement);
    await act(async () => {});
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
