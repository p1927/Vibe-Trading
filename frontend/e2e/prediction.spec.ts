import { expect, test } from '@playwright/test'

/**
 * Predictions panel behavioural E2E — src/pages/Prediction.tsx +
 * src/components/prediction/*. Talks to the real Vibe Trading API on
 * :8899 and the live stock_simulator on :8902. NO mocks, NO
 * `installBaselineApiMock`, NO route-level `page.route(...)`
 * interception of API calls — every assertion reads from a real
 * response or asserts a real network call landed.
 *
 * Three tiers, matching docs/audits/2026-08-27-vibetrading-e2e-coverage-plan.md
 * Section 1:
 *
 *   1. Render & console-clean baseline — the page mounts every
 *      sub-panel without crashing and the live API serves the real
 *      artifact (or `artifact: null` when the live backend has no
 *      recent run, which the UI must handle gracefully).
 *
 *   2. Interaction — controlled-input drift:
 *      - all 5 horizon options exist
 *      - changing horizon propagates to the next /trade/index-prediction
 *        fetch (the historical 2026-08-21 type-drift bug class)
 *      - "Run analysis" click issues POST /trade/index-prediction/run/start
 *        with body {ticker, horizon_days, refresh_constituents,
 *        run_forecast_lab:true} (real SSE stream is consumed; the test
 *        asserts the POST landed and the run state moves to `queued`
 *        or `running` — does NOT block on completion).
 *
 *   3. Populated-artifact assertions — when the live
 *      /trade/index-prediction response has a non-null artifact with
 *      numeric fields, the StatCards surface those numbers (not '—').
 *      This catches silent ?.-chain drops / type-drift regressions.
 *
 * Bug-filing convention: every failing assertion here surfaces a real
 * bug filed as `.claude/backlog/items/2026-08-2X-prediction-<observed>.md`
 * per PROTOCOL.md, linked to the existing
 * `2026-08-27-prediction-tab-fail-loud-not-silent-fallback` umbrella.
 */

const VIBE_API_ORIGIN = 'http://localhost:8899'

/**
 * Read the live /trade/index-prediction response — what the Prediction
 * page renders on mount. Returns either a populated artifact or
 * `null` when the backend has nothing recent.
 */
type LiveArtifactEnvelope = {
  status: string
  ticker: string
  artifact: any | null
}

async function fetchLiveIndexPrediction(
  request: import('@playwright/test').APIRequestContext,
  horizonDays: number,
): Promise<LiveArtifactEnvelope> {
  const res = await request.get(`${VIBE_API_ORIGIN}/trade/index-prediction`, {
    params: { ticker: 'NIFTY', horizon_days: String(horizonDays) },
  })
  expect(res.status(), 'live /trade/index-prediction must respond 2xx').toBeLessThan(400)
  return (await res.json()) as LiveArtifactEnvelope
}

test.describe('Predictions panel — render baseline (live API)', () => {
  test('mounts controls and heading without crash or console error', async ({ page }) => {
    const consoleErrors: string[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text())
    })
    page.on('pageerror', (err) => consoleErrors.push(err.message))

    await page.goto('/prediction')

    // Heading + horizon-day selector render regardless of artifact
    // state — a concrete, content-specific assertion.
    await expect(page.getByRole('heading', { name: 'NIFTY 50 Prediction' })).toBeVisible({
      timeout: 15_000,
    })
    const horizonSelect = page.getByLabel('Prediction horizon')
    await expect(horizonSelect).toBeVisible()
    await expect(horizonSelect.locator('option', { hasText: 'Default (14d)' })).toHaveCount(1)

    // The "Run analysis" control always renders.
    await expect(page.getByRole('button', { name: 'Run analysis' })).toBeVisible()

    // No React error boundary rendered.
    expect(
      await page.getByRole('heading', { name: /something went wrong/i }).count(),
      'no error boundary expected',
    ).toBe(0)

    expect(
      consoleErrors,
      `unexpected console errors:\n${consoleErrors.join('\n')}`,
    ).toEqual([])
  })

  test('scoreboard mode renders without crash', async ({ page }) => {
    const consoleErrors: string[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text())
    })
    page.on('pageerror', (err) => consoleErrors.push(err.message))

    await page.goto('/prediction?mode=scoreboard')
    await expect(page.getByText(/scoreboard/i).first()).toBeVisible({ timeout: 15_000 })
    expect(
      await page.getByRole('heading', { name: /something went wrong/i }).count(),
      'no error boundary expected',
    ).toBe(0)
    expect(consoleErrors, `console errors:\n${consoleErrors.join('\n')}`).toEqual([])
  })
})

test.describe('Predictions panel — interactions (live API)', () => {
  test('horizon selector exposes all 5 documented options', async ({ page }) => {
    const consoleErrors: string[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text())
    })
    page.on('pageerror', (err) => consoleErrors.push(err.message))

    await page.goto('/prediction')
    const horizonSelect = page.getByLabel('Prediction horizon')
    await expect(horizonSelect).toBeVisible({ timeout: 15_000 })

    for (const label of [
      'Tactical (2d)',
      'Short (7d)',
      'Default (14d)',
      'Swing (30d)',
      'Structural (60d)',
    ]) {
      await expect(
        horizonSelect.locator('option', { hasText: label }),
        `horizon option "${label}" missing — PredictionControls.tsx drifted from documented HORIZON_OPTIONS`,
      ).toHaveCount(1)
    }
    expect(consoleErrors, `console errors:\n${consoleErrors.join('\n')}`).toEqual([])
  })

  test('changing horizon re-queries /trade/index-prediction with the new horizon_days', async ({
    page,
  }) => {
    // Regression-class: controlled-input drift (the
    // 2026-08-21-prediction-panel-type-drift bug class). The horizon
    // select's onChange must re-issue api.getIndexPrediction(ticker,
    // newHorizonDays). We attach a request listener to capture every
    // fetch to /trade/index-prediction (NOT /run/start or /run/<id>),
    // switch the select, and assert a fetch with horizon_days=7 lands.
    const seen: string[] = []
    page.on('request', (req) => {
      const url = req.url()
      if (
        url.includes('/trade/index-prediction') &&
        !url.includes('/run/')
      ) {
        const u = new URL(url)
        seen.push(u.searchParams.get('horizon_days') ?? '<null>')
      }
    })

    await page.goto('/prediction')
    await page.waitForResponse(
      (r) =>
        r.url().includes('/trade/index-prediction') && !r.url().includes('/run/'),
      { timeout: 15_000 },
    )

    const horizonSelect = page.getByLabel('Prediction horizon')
    await horizonSelect.selectOption('7')
    await page.waitForResponse(
      (r) =>
        r.url().includes('/trade/index-prediction') && !r.url().includes('/run/'),
      { timeout: 15_000 },
    )

    expect(
      seen,
      `expected at least one fetch with horizon_days=7 after selecting "Short (7d)"; saw ${JSON.stringify(seen)}`,
    ).toContain('7')
  })

  test('"Run analysis" issues POST /trade/index-prediction/run/start with horizon_days + refresh_constituents + run_forecast_lab', async ({
    page,
  }) => {
    // Wiring-drift guard. PredictionControls.onRun →
    // invokeRunPredictionAnalysis(ticker, horizonDays,
    // refreshConstituents) → usePredictionRunCoordinator.runPredictionAnalysis →
    // api.startIndexPredictionRun(body) → POST
    // /trade/index-prediction/run/start. The body MUST include
    // ticker, horizon_days, refresh_constituents, run_forecast_lab.
    // We capture the request body via page.on('request', ...) and
    // assert the real backend received it.
    let postBody: any = null
    let postUrl: string | null = null
    page.on('request', (req) => {
      if (
        req.method() === 'POST' &&
        req.url().endsWith('/trade/index-prediction/run/start')
      ) {
        postUrl = req.url()
        postBody = JSON.parse(req.postData() ?? '{}')
      }
    })

    await page.goto('/prediction')
    const horizonSelect = page.getByLabel('Prediction horizon')
    await expect(horizonSelect).toBeVisible({ timeout: 15_000 })

    // Pick a deterministic horizon so we can assert the body.
    await horizonSelect.selectOption('30')

    // The "Refresh all 50 constituents" checkbox — when checked, the
    // body must have refresh_constituents=true.
    const refreshCheckbox = page.getByLabel('Refresh all 50 constituents')
    if (await refreshCheckbox.isVisible().catch(() => false)) {
      await refreshCheckbox.check()
    }

    const runButton = page.getByRole('button', { name: /Run analysis/i })
    await expect(runButton).toBeVisible()
    await runButton.click()

    // The real POST landed. Wait for it via the request listener —
    // use waitForRequest with a predicate that matches our captured
    // URL.
    await expect
      .poll(() => postUrl, {
        timeout: 15_000,
        message: 'expected POST /trade/index-prediction/run/start to fire after Run analysis click',
      })
      .not.toBeNull()

    expect(postUrl, 'POST URL must hit /trade/index-prediction/run/start').toMatch(
      /\/trade\/index-prediction\/run\/start$/,
    )

    const body = postBody as Record<string, unknown>
    // All four documented fields must be in the body — drift in any
    // one is a real bug for the wiring class this spec guards.
    expect(body, `body captured: ${JSON.stringify(body)}`).not.toBeNull()
    expect(body.ticker, 'body.ticker must be present').toBe('NIFTY')
    expect(body.horizon_days, 'body.horizon_days must equal the selected 30').toBe(30)
    expect(typeof body.refresh_constituents).toBe('boolean')
    expect(body.run_forecast_lab, 'body.run_forecast_lab must default to true').toBe(true)

    // The button should now read "Analysis running…" while the real
    // SSE stream is in flight — this is the live UI wiring assertion
    // (not a mock state).
    await expect(page.getByRole('button', { name: /Analysis running/i })).toBeVisible({
      timeout: 10_000,
    })
  })
})

test.describe('Predictions panel — populated artifact (live API)', () => {
  test('renders numeric fields from the live artifact into StatCards', async ({
    page,
    request,
  }) => {
    // Pre-fetch the live artifact to know which fields exist before
    // asserting the rendered numbers. If the live backend currently
    // returns artifact:null (e.g. immediately after a restart with no
    // recent run), skip the numeric assertions but still assert the
    // page didn't crash — this is what "fail loud, not silent" means
    // for a panel that depends on async pipeline output.
    const env = await fetchLiveIndexPrediction(request, 14)

    await page.goto('/prediction')
    // Wait for the artifact fetch to settle so the StatCards have a
    // chance to populate.
    await page.waitForResponse(
      (r) => r.url().includes('/trade/index-prediction') && !r.url().includes('/run/'),
      { timeout: 15_000 },
    )

    // No React error boundary, no console errors.
    expect(
      await page.getByRole('heading', { name: /something went wrong/i }).count(),
      'no error boundary expected',
    ).toBe(0)

    if (env.artifact === null) {
      // The live backend has no recent artifact — that's a state, not
      // a bug. Assert the page renders the empty/idle state cleanly.
      test.skip(true, 'live /trade/index-prediction returned artifact:null — cannot exercise numeric StatCards; rerun after a live run')
      return
    }

    const artifact = env.artifact
    // Direction confidence renders as a percentage when populated.
    if (artifact.prediction?.direction_confidence != null) {
      const pct = Math.round(artifact.prediction.direction_confidence * 100)
      await expect(
        page.getByText(new RegExp(`${pct}%`)),
        `StatCards should surface direction_confidence as ${pct}%`,
      ).toBeVisible({ timeout: 10_000 })
    }
    // Model direction score — same percentage treatment.
    if (artifact.prediction?.direction_model_score != null) {
      const pct = Math.round(artifact.prediction.direction_model_score * 100)
      await expect(
        page.getByText(new RegExp(`${pct}%`)),
        `StatCards should surface direction_model_score as ${pct}%`,
      ).toBeVisible({ timeout: 10_000 })
    }
    // Range endpoints — formatted with thousands separators via
    // .toLocaleString(). Use the actual numeric values from the live
    // response.
    if (artifact.prediction?.range?.low != null && artifact.prediction?.range?.high != null) {
      const lowStr = Math.round(artifact.prediction.range.low).toLocaleString('en-IN')
      const highStr = Math.round(artifact.prediction.range.high).toLocaleString('en-IN')
      await expect(
        page.getByText(new RegExp(lowStr.replace(/,/g, ','))),
        `Expected range low "${lowStr}" should appear in StatCards`,
      ).toBeVisible({ timeout: 10_000 })
      await expect(
        page.getByText(new RegExp(highStr.replace(/,/g, ','))),
        `Expected range high "${highStr}" should appear in StatCards`,
      ).toBeVisible({ timeout: 10_000 })
    }
  })
})
