import { expect, test, type Page } from '@playwright/test'
import { ARTIFACT, SCOREBOARD_STALE, installPredictionMock, type MockOverrides } from './helpers/predictionMock'

/**
 * Predictions panel behavioural E2E — src/pages/Prediction.tsx + src/components/prediction/*.
 *
 * Fully mocked (e2e/helpers/predictionMock.ts): every fetch/xhr/SSE request is answered by page.route, so the
 * spec needs no stack and can never reach a real service. The only write it performs is the 'Run analysis'
 * POST /trade/index-prediction/run/start, fulfilled by the mock; afterEach asserts it was the only write.
 * Run headless with `npx playwright test -c playwright.mock.config.ts` (vite on an ephemeral port).
 *
 * Tiers: render baseline per mode (analysis, scoreboard current/stale/empty, replay, Miscellaneous, forecast
 * engine), interactions (horizon select, Run analysis wiring), populated-artifact StatCards.
 */

type Mock = Awaited<ReturnType<typeof installPredictionMock>>

let mock: Mock
let consoleErrors: string[]

async function open(page: Page, url: string, overrides: MockOverrides = {}) {
  mock = await installPredictionMock(page, overrides)
  consoleErrors = []
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text())
  })
  page.on('pageerror', (err) => consoleErrors.push(err.message))
  await page.goto(url)
}

async function expectCleanMount(page: Page) {
  expect(
    await page.getByRole('heading', { name: /something went wrong/i }).count(),
    'no error boundary expected',
  ).toBe(0)
  expect(consoleErrors, `unexpected console errors:\n${consoleErrors.join('\n')}`).toEqual([])
}

test.afterEach(() => {
  expect(mock.unexpectedWrites, 'the mock refused a write the spec does not expect').toEqual([])
  for (const w of mock.writes) expect(w).toBe('POST /trade/index-prediction/run/start')
})

test.describe('Predictions panel — analysis mode', () => {
  test('mounts controls and heading without crash or console error', async ({ page }) => {
    await open(page, '/prediction')
    await expect(page.getByRole('heading', { name: 'NIFTY 50 Prediction' })).toBeVisible({ timeout: 15_000 })
    const horizonSelect = page.getByLabel('Prediction horizon')
    await expect(horizonSelect).toBeVisible()
    await expect(horizonSelect.locator('option', { hasText: 'Default (14d)' })).toHaveCount(1)
    await expect(page.getByRole('button', { name: 'Run analysis' })).toBeVisible()
    await expectCleanMount(page)
    expect(mock.gets.some((g) => g.startsWith('/trade/index-prediction/refresh-gate'))).toBe(true)
    expect(mock.writes, 'a mount must not write (gate says market closed, so no refresh POST)').toEqual([])
  })

  test('horizon selector exposes all 5 documented options', async ({ page }) => {
    await open(page, '/prediction')
    const horizonSelect = page.getByLabel('Prediction horizon')
    await expect(horizonSelect).toBeVisible({ timeout: 15_000 })
    for (const label of ['Tactical (2d)', 'Short (7d)', 'Default (14d)', 'Swing (30d)', 'Structural (60d)']) {
      await expect(
        horizonSelect.locator('option', { hasText: label }),
        `horizon option "${label}" missing — PredictionControls.tsx drifted from documented HORIZON_OPTIONS`,
      ).toHaveCount(1)
    }
    await expectCleanMount(page)
  })

  test('changing horizon re-queries /trade/index-prediction with the new horizon_days', async ({ page }) => {
    // Regression-class: controlled-input drift (2026-08-21-prediction-panel-type-drift).
    await open(page, '/prediction')
    await expect(page.getByLabel('Prediction horizon')).toBeVisible({ timeout: 15_000 })
    await page.getByLabel('Prediction horizon').selectOption('7')
    await expect
      .poll(
        () => mock.gets.filter((g) => g.startsWith('/trade/index-prediction?') && g.includes('horizon_days=7')).length,
        { message: `expected a /trade/index-prediction fetch with horizon_days=7; saw ${JSON.stringify(mock.gets)}` },
      )
      .toBeGreaterThan(0)
  })

  test('"Run analysis" POSTs run/start with ticker + horizon_days + refresh_constituents + run_forecast_lab, and it is the only write', async ({
    page,
  }) => {
    await open(page, '/prediction')
    const horizonSelect = page.getByLabel('Prediction horizon')
    await expect(horizonSelect).toBeVisible({ timeout: 15_000 })
    await horizonSelect.selectOption('30')
    const refreshCheckbox = page.getByLabel('Refresh all 50 constituents')
    if (await refreshCheckbox.isVisible().catch(() => false)) await refreshCheckbox.check()

    await page.getByRole('button', { name: /Run analysis/i }).click()
    await expect
      .poll(() => mock.writes, { timeout: 15_000, message: 'expected POST /trade/index-prediction/run/start' })
      .toEqual(['POST /trade/index-prediction/run/start'])

    const body = mock.runStartBody()
    expect(body, `body captured: ${JSON.stringify(body)}`).not.toBeNull()
    expect(body!.ticker).toBe('NIFTY')
    expect(body!.horizon_days, 'selected 30').toBe(30)
    expect(typeof body!.refresh_constituents).toBe('boolean')
    expect(body!.run_forecast_lab, 'defaults to true').toBe(true)

    await expect(page.getByRole('button', { name: /Analysis running/i })).toBeVisible({ timeout: 10_000 })
  })

  test('renders the numeric fields of the artifact into StatCards', async ({ page }) => {
    await open(page, '/prediction')
    const p = ARTIFACT.prediction
    await expect(page.getByText(new RegExp(`${Math.round(p.direction_confidence * 100)}%`)).first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText(new RegExp(`${Math.round(p.direction_model_score * 100)}%`)).first()).toBeVisible()
    await expect(page.getByText(/23,871/).first(), 'range low').toBeVisible()
    await expect(page.getByText(/25,129/).first(), 'range high').toBeVisible()
    await expectCleanMount(page)
  })

  test('a null artifact (no run yet) renders the idle page without crash', async ({ page }) => {
    await open(page, '/prediction', { '/trade/index-prediction': { status: 'ok', ticker: 'NIFTY', artifact: null } })
    await expect(page.getByRole('button', { name: 'Run analysis' })).toBeVisible({ timeout: 15_000 })
    await expectCleanMount(page)
  })
})

test.describe('Predictions panel — Track Scoreboard (scoreboard states + replay)', () => {
  test('current scoreboard mounts with its header and the backtest replay section', async ({ page }) => {
    await open(page, '/prediction?mode=scoreboard')
    await expect(page.getByText('Forecast track lab — scoreboard')).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText(/120 OOS eval dates/).first()).toBeVisible()
    await expect(page.getByText('Track forecast replay vs Nifty 50')).toBeVisible()
    await expect(page.getByTestId('backtest-replay-stale')).toHaveCount(0)
    expect(mock.gets.some((g) => g.startsWith('/trade/index-prediction/forecast-replay'))).toBe(true)
    await expectCleanMount(page)
  })

  test('stale cached scoreboard says why there is nothing to replay and never asks for a replay', async ({ page }) => {
    await open(page, '/prediction?mode=scoreboard', {
      '/trade/index-prediction/track-scoreboard': { status: 'ok', ticker: 'NIFTY', report: SCOREBOARD_STALE },
    })
    const stale = page.getByTestId('backtest-replay-stale')
    await expect(stale).toBeVisible({ timeout: 15_000 })
    await expect(stale).toContainText('stale')
    await expect(stale).toContainText('schema 5')
    expect(mock.gets.some((g) => g.startsWith('/trade/index-prediction/forecast-replay'))).toBe(false)
    await expectCleanMount(page)
  })

  test('no cached scoreboard shows the empty state with its actions', async ({ page }) => {
    await open(page, '/prediction?mode=scoreboard', {
      '/trade/index-prediction/track-scoreboard': { status: 'ok', ticker: 'NIFTY', report: null },
    })
    await expect(page.getByText(/No cached scoreboard yet/)).toBeVisible({ timeout: 15_000 })
    await expect(page.getByRole('button', { name: 'Open Analysis tab' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Recompute scoreboard' })).toBeVisible()
    await expectCleanMount(page)
  })

  test('a failed scoreboard fetch shows the error message, not a blank page', async ({ page }) => {
    await open(page, '/prediction?mode=scoreboard', {
      '/trade/index-prediction/track-scoreboard': { status: 'error', ticker: 'NIFTY', message: 'scoreboard cache unreadable (e2e)' },
    })
    await expect(page.getByText(/scoreboard cache unreadable \(e2e\)/).first()).toBeVisible({ timeout: 15_000 })
    expect(await page.getByRole('heading', { name: /something went wrong/i }).count()).toBe(0)
  })
})

test.describe('Predictions panel — Miscellaneous tab (external predictions)', () => {
  test('mounts the street-views panel with an empty snapshot, read-only', async ({ page }) => {
    await open(page, '/prediction?mode=external-predictions')
    await expect(page.getByRole('heading', { name: 'Miscellaneous — Street views' })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText(/No NIFTY 50 index forecasts found for 14d/)).toBeVisible()
    expect(mock.gets.some((g) => g.startsWith('/trade/index-prediction/external-predictions'))).toBe(true)
    expect(mock.writes).toEqual([])
    await expectCleanMount(page)
  })

  test('the tab button switches to it from Analysis and keeps ?mode in the URL', async ({ page }) => {
    await open(page, '/prediction')
    await page.getByRole('button', { name: 'Miscellaneous' }).click()
    await expect(page.getByRole('heading', { name: 'Miscellaneous — Street views' })).toBeVisible({ timeout: 15_000 })
    expect(page.url()).toContain('mode=external-predictions')
  })
})

test.describe('Predictions panel — Forecast Engine tab', () => {
  test('mounts the recipe selector and chart sections from the recipes list', async ({ page }) => {
    await open(page, '/prediction?mode=forecast-engine')
    await expect(page.getByText('Forecast engine (TimesFM 3.0)').first()).toBeVisible({ timeout: 15_000 })
    await expect(page.getByRole('button', { name: /nifty_daily_v1/ })).toBeVisible()
    await expect(page.getByText('NIFTY daily close recipe (e2e fixture)')).toBeVisible()
    expect(mock.gets.some((g) => g.startsWith('/trade/index-prediction/forecast-engine-recipes'))).toBe(true)
    expect(mock.writes).toEqual([])
    await expectCleanMount(page)
  })

  test('an empty recipe registry says so', async ({ page }) => {
    await open(page, '/prediction?mode=forecast-engine', {
      '/trade/index-prediction/forecast-engine-recipes': { status: 'ok', recipes: [] },
    })
    await expect(page.getByText('No forecast_engine recipes registered.')).toBeVisible({ timeout: 15_000 })
  })
})
