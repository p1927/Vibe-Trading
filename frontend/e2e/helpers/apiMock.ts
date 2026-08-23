import type { Page, Route } from '@playwright/test'

/**
 * The frontend talks to the Vibe agent API on port 8899 whenever it's served
 * from the Vite dev port (see `src/lib/apiBase.ts::resolveApiBase`). None of
 * our specs want a live agent/LLM backend, so every spec that touches a page
 * with backend reads installs this baseline mock first — it answers every
 * `/api-ish` GET on 8899 with a safe, minimally-shaped 200 response so pages
 * render their real (empty/idle) UI instead of erroring out, and leaves
 * console clean for the "no console errors" baseline check every spec makes.
 *
 * Playwright runs routes most-recently-registered-first, so a spec can layer
 * more specific `page.route(...)` calls *after* calling this to override
 * individual endpoints (e.g. to return a populated session or an SSE stream).
 */

const API_ORIGIN_PATTERN = '**/localhost:8899/**'

/**
 * `src/components/settings/QVerisSettings.tsx` fetches `/qveris/config` and
 * `/qveris/status` as same-origin relative paths instead of going through
 * `resolveApiBase()` like the rest of `src/lib/api.ts` does. In dev (Vite on
 * 5899) that means the request never reaches the 8899 API at all — it hits
 * the Vite dev server itself, which has no such route and answers 500. This
 * is a pre-existing app bug (see the graph-walk report), not something this
 * mock should paper over silently — but for every *other* spec's baseline
 * "no console errors" check to hold on any page that mounts Settings, this
 * still needs a same-origin mock so the 500 doesn't leak into unrelated
 * assertions. */
const QVERIS_PATTERN = '**/qveris/**'

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

/** Minimal, always-safe default responses for endpoints the app polls or
 *  fetches unconditionally on most pages (settings, live status, connectors,
 *  recording constituents, replay/session state, global markets, etc). */
function defaultResponseFor(pathname: string): unknown {
  if (pathname === '/settings/llm') {
    return {
      provider: 'openai',
      model_name: 'gpt-5',
      base_url: '',
      api_key_configured: true,
      api_key_required: false,
      temperature: 0.2,
      timeout_seconds: 60,
      max_retries: 2,
      reasoning_effort: 'medium',
      sse_timeout_seconds: 90,
      env_path: '.env',
      providers: [{ name: 'openai', label: 'OpenAI' }],
    }
  }
  if (pathname === '/live/status') {
    return { brokers: [], global_halted: false }
  }
  if (pathname === '/trading/connectors') {
    return { selected_profile: '', profiles: [] }
  }
  if (pathname === '/autonomous-agents') {
    return { agents: [] }
  }
  if (pathname === '/trade/recording/constituents') {
    return {
      status: 'ok',
      constituents: [
        { symbol: 'RELIANCE', name: 'Reliance Industries', sector: 'Energy', weight: 10.5 },
        { symbol: 'TCS', name: 'Tata Consultancy Services', sector: 'IT', weight: 4.2 },
        { symbol: 'HDFCBANK', name: 'HDFC Bank', sector: 'Financials', weight: 8.1 },
      ],
    }
  }
  if (pathname === '/trade/recording/replay/status' || pathname === '/trade/recording/replay/calendar') {
    return pathname.endsWith('calendar')
      ? { status: 'ok', days: [] }
      : { status: 'idle', armed: false }
  }
  if (pathname === '/trade/recording/active') {
    return { status: 'idle', job: null }
  }
  if (pathname === '/trade/recording/auto-record') {
    return { enabled: false }
  }
  if (pathname === '/trade/recording/sessions') {
    return { status: 'ok', sessions: [] }
  }
  if (pathname.startsWith('/trade/hub/market-data/spot')) {
    return {
      status: 'ok',
      symbol: 'NIFTY',
      exchange: 'NSE_INDEX',
      spot: {
        symbol: 'NIFTY',
        exchange: 'NSE_INDEX',
        ltp: 24500.5,
        prev_close: 24400.1,
        source: 'mock',
        as_of: new Date().toISOString(),
      },
      session_open: false,
    }
  }
  if (pathname.startsWith('/trade/hub/market-data/ticks')) {
    return { status: 'ok', symbol: 'NIFTY', exchange: 'NSE_INDEX', source: 'mock', ticks: [] }
  }
  if (pathname.startsWith('/trade/hub/stock-history/coverage')) {
    return {
      status: 'ok',
      week_start: '',
      week_end: '',
      symbol: 'NIFTY',
      is_complete: true,
      missing_days: [],
      bucket_labels: [],
      days: [],
      fetch_list: [],
    }
  }
  if (pathname.startsWith('/trade/hub/')) {
    return { status: 'ok' }
  }
  if (pathname === '/scheduled-runs') {
    return []
  }
  if (pathname === '/scheduled-runs/scheduler/status') {
    return { enabled: false, running: false }
  }
  if (pathname === '/sessions') {
    return []
  }
  if (pathname === '/alpha/list') {
    return { alphas: [] }
  }
  if (pathname.startsWith('/trade/index-prediction')) {
    return { status: 'ok', ticker: 'NIFTY', artifact: null }
  }
  // Generic fallback: most consumers guard on `res.status === "ok"` or use
  // `?? []` / optional chaining on array/record fields, so an empty "ok"
  // envelope is safe for anything not special-cased above.
  return { status: 'ok' }
}

/** Installs the baseline network mock for the Vibe API (port 8899). Call
 *  this first in a spec, before any page.goto/navigation. */
export async function installBaselineApiMock(page: Page): Promise<void> {
  await page.route(API_ORIGIN_PATTERN, async (route) => {
    const url = new URL(route.request().url())
    if (route.request().method() !== 'GET') {
      // OptionsLab.tsx auto-analyzes a default payoff on mount and renders
      // `result.summary/.greeks/.limitations` unconditionally once `result`
      // is non-null (src/pages/OptionsLab.tsx). A non-2xx response here
      // logs a "Failed to load resource" console entry regardless of how
      // gracefully the app handles it (that's a browser-level log, not a JS
      // error), which would trip every spec's "no console errors" baseline
      // — so this needs a real, fully-shaped `OptionsPayoffResponse`
      // (src/lib/options.ts) rather than an error response.
      if (url.pathname === '/options/payoff') {
        return json(route, {
          status: 'ok',
          inputs: {},
          summary: {
            net_premium: 0,
            entry_commission: 0,
            entry_cost: 0,
            entry_side: 'flat',
            breakevens: [],
            breakeven_intervals: [],
            max_profit: null,
            max_loss: null,
            profit_unbounded: false,
            loss_unbounded: false,
          },
          greeks: { delta: 0, gamma: 0, theta: 0, vega: 0, rho: 0 },
          expiry_curve: { spot: [], pnl: [] },
          scenario_grid: { iv_values: [], spot: [], pnl: [] },
          limitations: [],
        })
      }
      return json(route, { status: 'ok' })
    }
    // Agent.tsx's `loadGoalSnapshot` fetches `GET /sessions/:id/goal` on
    // every session mount and treats a 404 as "no goal attached to this
    // session" (its own explicit, already-handled empty state — see
    // `getGoalProgress`'s `snapshot?.criteria.length` in src/pages/Agent.tsx,
    // which needs `snapshot` itself to be null/absent, not a truthy object
    // missing the `GoalSnapshot` shape). A real backend never returns
    // `{status:"ok"}` without the full shape for this endpoint, so answering
    // with the generic envelope here would be a self-inflicted crash, not a
    // faithful stand-in for "no live backend".
    if (url.pathname.endsWith('/goal')) {
      return json(route, { detail: 'Not found' }, 404)
    }
    return json(route, defaultResponseFor(url.pathname))
  })

  await page.route(QVERIS_PATTERN, async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/qveris/status')) {
      return json(route, {
        enabled: false,
        ok: false,
        error: null,
        remaining_credits: null,
        recent: [],
        signup_url: 'https://qveris.ai/signup',
        invite_code: '',
      })
    }
    return json(route, {
      enabled: false,
      base_url: 'https://qveris.ai/api/v1',
      api_key_masked: '',
      mode: 'free',
      budget_credits_per_session: 0,
      configured: false,
      signup_url: 'https://qveris.ai/signup',
      invite_code: '',
    })
  })
}

/** Collects console errors and page errors for the "no console errors"
 *  baseline assertion every spec makes. Call once per test, then assert
 *  `errors.length === 0` (or filter known-benign entries) at the end. */
export function trackConsoleErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text())
  })
  page.on('pageerror', (err) => {
    errors.push(err.message)
  })
  return errors
}

/** True if the React route error boundary (RouteErrorFallback) rendered. */
export async function hasErrorBoundary(page: Page): Promise<boolean> {
  return (await page.getByRole('heading', { name: /something went wrong/i }).count()) > 0
}
