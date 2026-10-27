import type { Page, Route } from '@playwright/test'

/**
 * Network mock for e2e/prediction.spec.ts. EVERY fetch/xhr/SSE request the page makes is answered here
 * (matched by resource type, so the API origin does not matter), nothing is passed to a real service.
 * GETs return fixtures shaped like the real /trade/index-prediction payloads; the one write the spec
 * performs, POST /trade/index-prediction/run/start, is fulfilled with a queued job. Any other non-GET is
 * refused with a 500 and recorded in `unexpectedWrites` so the spec can fail on it.
 */

export const ARTIFACT = {
  ticker: 'NIFTY',
  as_of: '2026-10-26T10:00:00+00:00',
  horizon_days: 14,
  spot: 24500,
  prediction: {
    direction: 'up',
    direction_confidence: 0.63,
    direction_model_score: 0.57,
    expected_return_pct: 1.2,
    range: { low: 23871, high: 25129, half_width_pct: 2.57, band_source: 'walk_forward_oos' },
  },
}

export const SCOREBOARD_CURRENT = {
  status: 'ok',
  ticker: 'NIFTY',
  horizon_days: 14,
  needs_refresh: false,
  as_of: '2026-10-25T09:00:00+00:00',
  schema_version: 6,
  history_start: '2025-01-01',
  history_end: '2026-10-20',
  eval_count: 120,
  tracks: { quant_ridge: { track_id: 'quant_ridge', eval_count: 120, mae_pct: 2.1, direction_hit_rate: 0.56 } },
}

export const SCOREBOARD_STALE = { ...SCOREBOARD_CURRENT, needs_refresh: true, as_of: '2026-08-29T13:29:45+00:00', schema_version: 5 }

export type MockOverrides = Record<string, unknown | ((url: URL) => unknown)>

export interface PredictionMock {
  /** every GET path (with query) the page requested */
  gets: string[]
  /** every non-GET as "METHOD path" that was fulfilled by the mock (only run/start is expected) */
  writes: string[]
  /** every non-GET the mock refused */
  unexpectedWrites: string[]
  runStartBody: () => Record<string, unknown> | null
}

function fixtureFor(path: string): unknown {
  if (path === '/trade/index-prediction') return { status: 'ok', ticker: 'NIFTY', artifact: ARTIFACT }
  // The live-refresh gate says the market is closed, as it does off-hours: the page then sends no refresh POST.
  if (path === '/trade/index-prediction/refresh-gate') return { ticker: 'NIFTY', skip_reason: 'market_closed' }
  if (path === '/trade/index-prediction/run/active') return { status: 'ok', job: null }
  if (/^\/trade\/index-prediction\/run\/[^/]+$/.test(path)) {
    return { status: 'ok', job: { job_id: 'job-e2e-1', status: 'running', ticker: 'NIFTY', logs: [] } }
  }
  if (path === '/trade/index-prediction/track-scoreboard') return { status: 'ok', ticker: 'NIFTY', report: SCOREBOARD_CURRENT }
  if (path === '/trade/index-prediction/forecast-replay') {
    return { status: 'ok', ticker: 'NIFTY', actuals: [], sources: [], records: [], stats: {} }
  }
  if (path === '/trade/index-prediction/external-predictions') {
    return { status: 'ok', ticker: 'NIFTY', snapshot: { symbol: 'NIFTY', horizon_days: 14, sources: [], predictions: [] } }
  }
  if (path === '/trade/index-prediction/forecast-engine-recipes') {
    return {
      status: 'ok',
      default_recipe: 'nifty_daily_v1',
      recipes: [{ name: 'nifty_daily_v1', version: '1', description: 'NIFTY daily close recipe (e2e fixture)' }],
    }
  }
  if (path === '/trade/index-prediction/history' || path === '/trade/index-prediction/forecast-engine-runs') {
    return { status: 'ok', ticker: 'NIFTY', history: [], runs: [] }
  }
  if (path === '/settings/llm') return { provider: 'openai', model_name: 'gpt-5', providers: [] }
  if (path === '/live/status') return { brokers: [], global_halted: false }
  if (path === '/trading/connectors') return { selected_profile: '', profiles: [] }
  if (path === '/scheduled-runs' || path === '/sessions') return []
  // Generic envelope: most consumers guard on status === 'ok' and use `?? []` on list fields.
  return { status: 'ok' }
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

export async function installPredictionMock(page: Page, overrides: MockOverrides = {}): Promise<PredictionMock> {
  const mock: PredictionMock = {
    gets: [],
    writes: [],
    unexpectedWrites: [],
    runStartBody: () => runBody,
  }
  let runBody: Record<string, unknown> | null = null

  await page.route(
    '**/*',
    async (route) => {
      const req = route.request()
      if (!['fetch', 'xhr', 'eventsource'].includes(req.resourceType())) return route.fallback() // page, vite modules, assets
      const url = new URL(req.url())
      const path = url.pathname
      const label = `${req.method()} ${path}`

      if (req.method() !== 'GET') {
        if (req.method() === 'POST' && path === '/trade/index-prediction/run/start') {
          runBody = JSON.parse(req.postData() ?? '{}')
          mock.writes.push(label)
          return json(route, { status: 'ok', job_id: 'job-e2e-1', job_status: 'queued' })
        }
        mock.unexpectedWrites.push(label)
        return json(route, { detail: `e2e mock refused ${label}` }, 500)
      }

      mock.gets.push(path + url.search)
      if (/\/stream$/.test(path)) {
        // An SSE stream that ends with no `done` event: the client falls back to polling the job snapshot.
        return route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': e2e\n\n' })
      }
      const o = overrides[path]
      return json(route, typeof o === 'function' ? (o as (u: URL) => unknown)(url) : (o ?? fixtureFor(path)))
    },
  )
  return mock
}
