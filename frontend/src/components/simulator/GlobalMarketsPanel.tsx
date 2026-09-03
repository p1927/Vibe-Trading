import { useCallback, useEffect, useRef, useState } from "react";
import { api, type GlobalMacroRow, type GlobalMacroUiCard, type MarketRegistryEntry } from "@/lib/api";
import { cn } from "@/lib/utils";
import { IndexCard } from "./IndexCard";
import { fetchMarketBundle, findBundleEntry } from "./marketBundleCache";

export const COUNTRY_LABELS: Record<string, string> = {
  IN: "India",
  US: "US",
  CN: "China",
  JP: "Japan",
  RU: "Russia",
  ME: "Middle East",
  LATAM: "Latin America",
  EU: "Europe",
};

// Order mirrors `market_registry.SUPPORTED_MARKETS`, plus Currencies (the 6 USD-anchored FX
// pairs), Global (cross-market factors), Economy (cross-market GDP/fiscal/unemployment/etc.
// comparison, `EconomyPanel`) — all three fronting series that aren't owned by one market and so
// have no index-card grid of their own — and Multi-Market (cross-market simultaneous replay).
const TAB_ORDER = ["IN", "US", "CN", "JP", "RU", "ME", "LATAM", "EU", "CURRENCY", "GLOBAL", "ECONOMY", "MULTI"];

// CURRENCY/GLOBAL tab card lists used to be hand-maintained here (and drifted out of sync with
// `factors/catalog.py` — see .claude/backlog/items/2026-09-03-global-factors-registry-not-wired-to-consumers.md).
// Both now come from `api.getGlobalMacroUiCards()`, which is generated backend-side from the
// factor registry + `market_registry.py` (`StockHistory.global_market_ui_cards()`) — see
// `emptyUiCards`'s fetch below.
interface UiCards {
  global: GlobalMacroUiCard[];
  currency: GlobalMacroUiCard[];
}

const EMPTY_UI_CARDS: UiCards = { global: [], currency: [] };

// India's headline indices aren't served by the `/trade/markets/{country}/...`
// dispatch (the backend rejects country="IN" there — it has its own dedicated
// methods instead), so these cards go through the same `/trade/hub/*` live
// endpoints the "Chart symbol" panel above already uses. GIFT Nifty's exchange
// is "NSEIX" (NSE International Exchange, GIFT City), not "NSE_INDEX" like the
// other three — it's a genuinely different exchange, not a formatting quirk.
const INDIA_INDICES: { name: string; symbol: string; exchange: string }[] = [
  { name: "NIFTY 50", symbol: "NIFTY", exchange: "NSE_INDEX" },
  { name: "SENSEX", symbol: "SENSEX", exchange: "NSE_INDEX" },
  { name: "BANK NIFTY", symbol: "BANKNIFTY", exchange: "NSE_INDEX" },
  { name: "GIFT NIFTY", symbol: "GIFTNIFTY", exchange: "NSEIX" },
];

interface CardState {
  key: string;
  name: string;
  price: number | null;
  change: number | null;
  changePct: number | null;
  sparkline: number[];
  badge: string;
  loading: boolean;
  error: string | null;
}

function deriveChange(price: number | null, prevClose: number | null): { change: number | null; changePct: number | null } {
  if (price == null || prevClose == null) return { change: null, changePct: null };
  const change = price - prevClose;
  return { change, changePct: prevClose ? (change / prevClose) * 100 : null };
}

/** Market-overview strip: country tabs, each showing a card grid of that
 * country's headline indices (price, change, sparkline) — the compact
 * "quick glance" view. `activeTab`/`onTabChange` are controlled by the parent
 * (`Simulator.tsx`) so it can show different Record/Replay/Coverage sections
 * below depending on which market is selected here. */
export function GlobalMarketsPanel({
  activeTab,
  onTabChange,
}: {
  activeTab: string;
  onTabChange: (code: string) => void;
}) {
  const [registry, setRegistry] = useState<MarketRegistryEntry[]>([]);
  const [registryError, setRegistryError] = useState<string | null>(null);
  const [cards, setCards] = useState<CardState[]>([]);
  const [uiCards, setUiCards] = useState<UiCards>(EMPTY_UI_CARDS);
  const [uiCardsError, setUiCardsError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getMarketRegistry()
      .then((res) => setRegistry(res.markets))
      .catch((err) => setRegistryError(err instanceof Error ? err.message : String(err)));
    api
      .getGlobalMacroUiCards()
      .then((res) => setUiCards(res.data))
      .catch((err) => setUiCardsError(err instanceof Error ? err.message : String(err)));
  }, []);

  const loadIndia = useCallback(() => {
    setCards((prev) =>
      INDIA_INDICES.map((idx) => {
        const existing = prev.find((c) => c.key === idx.symbol);
        return existing
          ? { ...existing, loading: true, error: null }
          : { key: idx.symbol, name: idx.name, price: null, change: null, changePct: null, sparkline: [], badge: "LIVE", loading: true, error: null };
      }),
    );
    // Bounded to a recent window (not the endpoint's unbounded 2015-2026 default): a
    // sparkline only needs the last few trading days, and NIFTY alone has a fully
    // recorded multi-year 1-min bundle (BANKNIFTY/SENSEX have far less history), so an
    // unbounded request for NIFTY returns ~487k rows (~130MB) and stalls the card
    // indefinitely while its siblings load fine — see
    // 2026-08-27-global-markets-india-card-unbounded-history-fetch.
    const untilIst = new Date().toISOString();
    const sinceIst = new Date(Date.now() - 5 * 24 * 60 * 60 * 1000).toISOString();
    INDIA_INDICES.forEach((idx) => {
      Promise.all([
        api.getHubMarketDataSpot({ symbol: idx.symbol, exchange: idx.exchange }),
        api.getHubIndexHistoryBars({ symbol: idx.symbol, exchange: idx.exchange, since_ist: sinceIst, until_ist: untilIst }),
      ])
        .then(([spotRes, barsRes]) => {
          const bars = barsRes.bars ?? [];
          const sparkline = bars.map((b) => b.close).filter((v) => typeof v === "number");
          const price = spotRes.spot?.ltp ?? (sparkline.length ? sparkline[sparkline.length - 1] : null);
          const prevClose = spotRes.spot?.prev_close ?? (sparkline.length ? sparkline[0] : null);
          const { change, changePct } = deriveChange(price ?? null, prevClose ?? null);
          setCards((prev) =>
            prev.map((c) => (c.key === idx.symbol ? { ...c, price: price ?? null, change, changePct, sparkline, loading: false } : c)),
          );
        })
        .catch((err) => {
          const message = err instanceof Error ? err.message : String(err);
          setCards((prev) => prev.map((c) => (c.key === idx.symbol ? { ...c, loading: false, error: message } : c)));
        });
    });
  }, []);

  // Tracks which country's cards are currently on screen so a bundle response that resolves
  // after the user has already switched tabs again doesn't overwrite the newer tab's cards.
  const activeCountryRef = useRef<string | null>(null);

  const loadCountry = useCallback(
    (country: string, opts?: { force?: boolean }) => {
      const market = registry.find((m) => m.code === country);
      if (!market) return;
      activeCountryRef.current = country;
      setCards((prev) =>
        market.indices.map((idx) => {
          const existing = prev.find((c) => c.key === idx);
          return existing
            ? { ...existing, loading: true, error: null }
            : { key: idx, name: idx, price: null, change: null, changePct: null, sparkline: [], badge: "EOD", loading: true, error: null };
        }),
      );
      // One request for every headline + sector index in this market (see marketBundleCache),
      // replacing what used to be one `getMarketIndexHistory` call per headline index.
      fetchMarketBundle(country, opts)
        .then((data) => {
          if (activeCountryRef.current !== country) return;
          setCards(
            market.indices.map((idx) => {
              const entry = findBundleEntry(data, idx);
              if (!entry) {
                return { key: idx, name: idx, price: null, change: null, changePct: null, sparkline: [], badge: "EOD", loading: false, error: "not in bundle" };
              }
              const closes = entry.rows.map((r) => r.close).filter((v): v is number => typeof v === "number");
              const price = closes.length ? closes[closes.length - 1] : null;
              const prevClose = closes.length > 1 ? closes[closes.length - 2] : null;
              const { change, changePct } = deriveChange(price, prevClose);
              return {
                key: idx, name: idx, price, change, changePct,
                sparkline: closes.slice(-30), badge: "EOD", loading: false, error: entry.error,
              };
            }),
          );
        })
        .catch((err) => {
          if (activeCountryRef.current !== country) return;
          const message = err instanceof Error ? err.message : String(err);
          setCards((prev) => prev.map((c) => ({ ...c, loading: false, error: message })));
        });
    },
    [registry],
  );

  // Shared by loadCurrencies/loadGlobal — both tabs' cards are `global_macro_store` series that
  // read the same way (live-spot-if-any + history), differing only in which card list feeds them.
  const loadMacroCards = useCallback((factors: GlobalMacroUiCard[]) => {
    setCards((prev) =>
      factors.map((f) => {
        const existing = prev.find((c) => c.key === f.key);
        const badge = f.live_spot_series ? "LIVE" : "EOD";
        return existing
          ? { ...existing, loading: true, error: null }
          : { key: f.key, name: f.name, price: null, change: null, changePct: null, sparkline: [], badge, loading: true, error: null };
      }),
    );
    factors.forEach((f) => {
      const historyPromise = api.getGlobalMacroHistory(f.key, { field: f.field });
      const spotPromise = f.live_spot_series ? api.getGlobalMacroLiveSpot(f.live_spot_series) : Promise.resolve(null);
      Promise.all([spotPromise, historyPromise])
        .then(([spotRes, histRes]) => {
          const rows: GlobalMacroRow[] = Array.isArray(histRes.data) ? histRes.data : [];
          const closes = rows.map((r) => r.value).filter((v): v is number => typeof v === "number");
          const liveValue = spotRes?.data?.value ?? null;
          // When a live spot is available it's "now" and the latest history close is "the
          // reference point to diff against"; without one, price falls back to the latest
          // close itself, so the diff needs the *previous* close instead — using the same
          // closes[len-1] reference in both cases would compare a value against itself.
          const price = liveValue ?? (closes.length ? closes[closes.length - 1] : null);
          const prevClose = liveValue != null
            ? (closes.length ? closes[closes.length - 1] : null)
            : (closes.length > 1 ? closes[closes.length - 2] : null);
          const { change, changePct } = deriveChange(price, prevClose);
          setCards((prev) =>
            prev.map((c) => (c.key === f.key ? { ...c, price, change, changePct, sparkline: closes.slice(-30), loading: false } : c)),
          );
        })
        .catch((err) => {
          const message = err instanceof Error ? err.message : String(err);
          setCards((prev) => prev.map((c) => (c.key === f.key ? { ...c, loading: false, error: message } : c)));
        });
    });
  }, []);

  const loadCurrencies = useCallback(() => loadMacroCards(uiCards.currency), [loadMacroCards, uiCards.currency]);
  const loadGlobal = useCallback(() => loadMacroCards(uiCards.global), [loadMacroCards, uiCards.global]);

  const reload = useCallback(() => {
    if (activeTab === "CURRENCY") loadCurrencies();
    else if (activeTab === "GLOBAL") loadGlobal();
    else if (activeTab === "ECONOMY" || activeTab === "MULTI") setCards([]);
    else if (activeTab === "IN") loadIndia();
    else loadCountry(activeTab, { force: true });
  }, [activeTab, loadIndia, loadCountry, loadCurrencies, loadGlobal]);

  useEffect(() => {
    if (activeTab === "CURRENCY") {
      if (uiCards.currency.length === 0) return;
      loadCurrencies();
      return;
    }
    if (activeTab === "GLOBAL") {
      if (uiCards.global.length === 0) return;
      loadGlobal();
      return;
    }
    if (activeTab === "ECONOMY") {
      // No index-card grid — EconomyPanel (rendered by Simulator.tsx) has its own
      // cross-market factor picker + chart, fetched independently.
      setCards([]);
      return;
    }
    if (activeTab === "MULTI") {
      // No index-card grid — MultiMarketReplayPanel (rendered by Simulator.tsx) shows
      // per-market status for whichever markets the user arms.
      setCards([]);
      return;
    }
    if (activeTab === "IN") {
      loadIndia();
      return;
    }
    if (registry.length === 0) return;
    loadCountry(activeTab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, registry, uiCards]);

  useEffect(() => {
    const interval = window.setInterval(reload, 30_000);
    return () => window.clearInterval(interval);
  }, [reload]);

  return (
    <div>
      <div className="flex flex-wrap items-center gap-1 border-b pb-2">
        {TAB_ORDER.map((code) => (
          <button
            key={code}
            type="button"
            onClick={() => onTabChange(code)}
            className={cn(
              "rounded-full px-3 py-1 text-xs font-medium transition-colors",
              activeTab === code ? "bg-foreground text-background" : "text-muted-foreground hover:bg-accent",
            )}
          >
            {code === "CURRENCY"
              ? "Currencies"
              : code === "GLOBAL"
              ? "Global"
              : code === "ECONOMY"
              ? "Economy"
              : code === "MULTI"
              ? "Multi-Market"
              : COUNTRY_LABELS[code] ?? code}
          </button>
        ))}
      </div>
      {registryError && <p className="mt-2 text-[11px] text-destructive">{registryError}</p>}
      {uiCardsError && <p className="mt-2 text-[11px] text-destructive">{uiCardsError}</p>}
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {cards.map((c) => (
          <IndexCard
            key={c.key}
            name={c.name}
            price={c.price}
            change={c.change}
            changePct={c.changePct}
            sparkline={c.sparkline}
            badge={c.badge}
            loading={c.loading}
            error={c.error}
          />
        ))}
      </div>
    </div>
  );
}
