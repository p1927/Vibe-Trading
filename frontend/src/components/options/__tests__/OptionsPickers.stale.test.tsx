import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { IndiaUnderlyingSelect } from "../IndiaUnderlyingSelect";
import { OptionsChainTable } from "../OptionsChainTable";
import { api } from "@/lib/api";
import { MARKETS } from "@/lib/marketConfig";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("IndiaUnderlyingSelect source switch", () => {
  it("does not keep the previous source's equities when the new source's list fails to load", async () => {
    vi.spyOn(api, "getIndiaUnderlyings")
      .mockResolvedValueOnce({ ok: true, data: { indexes: ["NIFTY"], equities: ["TCS"] } } as never)
      .mockRejectedValueOnce(new Error("source b down"));
    const { rerender } = render(<IndiaUnderlyingSelect source={"stock_simulator" as never} value="NIFTY" onChange={() => {}} />);
    expect(await screen.findByRole("option", { name: "TCS" })).toBeInTheDocument();
    rerender(<IndiaUnderlyingSelect source={"openalgo" as never} value="NIFTY" onChange={() => {}} />);
    await waitFor(() => expect(screen.getByLabelText("Underlying")).toHaveAttribute("title", expect.stringContaining("source b down")));
    expect(screen.queryByRole("option", { name: "TCS" })).toBeNull();
  });

  it("does not keep them when the new source answers with no list", async () => {
    vi.spyOn(api, "getIndiaUnderlyings")
      .mockResolvedValueOnce({ ok: true, data: { indexes: ["NIFTY"], equities: ["TCS"] } } as never)
      .mockResolvedValueOnce({ ok: false } as never);
    const { rerender } = render(<IndiaUnderlyingSelect source={"stock_simulator" as never} value="NIFTY" onChange={() => {}} />);
    await screen.findByRole("option", { name: "TCS" });
    rerender(<IndiaUnderlyingSelect source={"openalgo" as never} value="NIFTY" onChange={() => {}} />);
    await waitFor(() => expect(screen.getByLabelText("Underlying")).toHaveAttribute("title", expect.stringContaining("no list")));
    expect(screen.queryByRole("option", { name: "TCS" })).toBeNull();
  });
});

describe("OptionsChainTable", () => {
  it("Retry with a blanked ticker box asks for the default ticker, not an empty one", async () => {
    const chain = vi.spyOn(api, "getOptionsChain").mockResolvedValue({ ok: false, error: "chain down" } as never);
    render(<OptionsChainTable market="us" source={"stock_simulator" as never} />);
    await screen.findByText(/chain down/);
    fireEvent.change(screen.getByLabelText("Ticker"), { target: { value: "  " } });
    chain.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(chain).toHaveBeenCalled());
    expect(chain.mock.calls[0][0]).toBe(MARKETS.us.defaultTicker);
  });

  it("shows the underlying level in the Indian format", async () => {
    vi.spyOn(api, "getOptionsChain").mockResolvedValue({
      ok: true,
      source: "stock_simulator",
      data: { ticker: "NIFTY", expiration: 1, expirations: [1], calls: [], puts: [], underlying_ltp: 1234567.5 },
    } as never);
    render(<OptionsChainTable market="us" source={"stock_simulator" as never} />);
    expect(await screen.findByTestId("options-chain-source")).toHaveTextContent("12,34,567.5");
  });
});
