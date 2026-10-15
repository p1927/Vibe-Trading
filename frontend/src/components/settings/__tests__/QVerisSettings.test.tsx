import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import i18n from "@/i18n";
import { QVerisSettings } from "@/components/settings/QVerisSettings";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function stubFetch(config: Record<string, unknown> = {}) {
  const base = {
    enabled: true,
    base_url: "https://qveris.ai/api/v1",
    api_key_masked: "sk-...8TI",
    mode: "paid",
    budget_credits_per_session: 50,
    configured: true,
    signup_url: "https://qveris.ai/?ref=ok",
    invite_code: "INV",
    ...config,
  };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path === "/qveris/config" && init?.method === "PUT") return json({ ...base, ...JSON.parse(String(init.body)) });
    if (path === "/qveris/config") return json(base);
    if (path === "/qveris/status") return json({ enabled: true, ok: true, error: null, remaining_credits: 1, recent: [], signup_url: "", invite_code: "" });
    return json({ detail: path }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const puts = (fetchMock: ReturnType<typeof stubFetch>) =>
  fetchMock.mock.calls.filter(([path, init]) => String(path) === "/qveris/config" && (init as RequestInit | undefined)?.method === "PUT");

describe("QVerisSettings budget and signup link", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  async function ready() {
    await waitFor(() => expect(screen.getByRole("button", { name: "Save QVeris settings" })).toBeEnabled());
  }

  it("an empty budget field is a missing value, never 0 credits: nothing is saved", async () => {
    const fetchMock = stubFetch();
    render(<QVerisSettings />);
    await ready();
    fireEvent.change(screen.getByDisplayValue("50"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save QVeris settings" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(puts(fetchMock)).toHaveLength(0);
  });

  it("a negative or fractional budget is refused too", async () => {
    for (const bad of ["-5", "1.5"]) {
      const fetchMock = stubFetch();
      const { unmount } = render(<QVerisSettings />);
      await ready();
      fireEvent.change(screen.getByDisplayValue("50"), { target: { value: bad } });
      fireEvent.click(screen.getByRole("button", { name: "Save QVeris settings" }));
      expect(await screen.findByRole("alert")).toBeInTheDocument();
      expect(puts(fetchMock)).toHaveLength(0);
      unmount();
    }
  });

  it("an explicit 0 budget is still a valid value and is sent as 0", async () => {
    const fetchMock = stubFetch();
    render(<QVerisSettings />);
    await ready();
    fireEvent.change(screen.getByDisplayValue("50"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Save QVeris settings" }));
    await waitFor(() => expect(puts(fetchMock)).toHaveLength(1));
    expect(JSON.parse(String((puts(fetchMock)[0][1] as RequestInit).body)).budget_credits_per_session).toBe(0);
  });

  it("a signup url that is not http(s) renders no link target", async () => {
    stubFetch({ signup_url: "javascript:alert(1)" });
    render(<QVerisSettings />);
    await ready();
    expect(screen.getByText("Open signup").closest("a")).not.toHaveAttribute("href");
  });
});
