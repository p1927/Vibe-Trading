const viteEnv = (import.meta as ImportMeta & { env?: { VITE_TRADE_UI_URL?: string } }).env;
const tradeUiUrl = (viteEnv?.VITE_TRADE_UI_URL || "http://127.0.0.1:8080").replace(/\/+$/, "");

export function tradeUiDeepLink(tab: "openalgo" | "vibe", path = "/"): string {
  const params = new URLSearchParams({ tab, path });
  return `${tradeUiUrl}/?${params.toString()}`;
}
