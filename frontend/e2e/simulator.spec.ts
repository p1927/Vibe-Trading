import { expect, test } from '@playwright/test'
import { hasErrorBoundary, installBaselineApiMock, trackConsoleErrors } from './helpers/apiMock'

/**
 * Stock Simulator behavioural E2E — src/pages/Simulator.tsx + the
 * src/components/simulator/* panels. Extends the existing
 * e2e/equity-picker.spec.ts (one assertion: equity picker propagates
 * to the live panel) with interaction assertions covering the user's
 * main flow on /simulator:
 *
 *   1. Render & console-clean baseline (the simulator page mounts all
 *      sub-panels — SimulatorLiveIndexPanel, the replay calendar/clock,
 *      equity picker, MultiMarketReplayPanel etc. — without crashing).
 *
 *   2. Interaction: pick a recorded day → "Arm replay" → POST
 *      `/trade/recording/<day>/replay` → chart source badge flips from
 *      "LIVE" to "REPLAY" (regression-class: badge drift; arm/replay
 *      semantics — the historical bug class for this page).
 *
 *   3. Interaction: "Stop replay" → POST `/trade/recording/replay/stop`
 *      → badge flips back.
 *
 *   4. Populated artifact: with `/trade/hub/market-data/ticks?replay=1`
 *      returning a populated tick list, the chart's LTP updates from
 *      that data (regression-class: silent fallback to spot.ltp when
 *      replay ticks are present).
 *
 * Note: this section is the **primary target** for the cross-process
 * env-mirror trap documented in the `bug-bash-remediation` skill — the
 * SimClock state lives in OpenAlgo's process, but the Vibe proxy on
 * :8899 mirrors neither `STOCK_SIMULATOR_MODE` nor `NSE_REPLAY_DATE` on
 * its own (per the cross-process trap table). What this spec verifies
 * is the *frontend contract* (the badge + UI updates); the *backend
 * trap* lives in `2026-08-27-cross-process-env-mirror-vibe-proxy` which
 * the probe_cross_process_env.py script in the skill is the dedicated
 * detector for.
 *
 * Bug filing convention: every failing assertion here surfaces a real
 * bug filed as `.claude/backlog/items/2026-08-27-simulator-<observed>.md`
 * per PROTOCOL.md.
 */

// Populated ReplayStatus shape — what `api.startReplay` returns on
// success (src/pages/Simulator.tsx armReplay() reads res.replay?.clock).
const ARMED_REPLAY_RESPONSE = {
  status: 'ok',
  message: 'armed',
  replay: {
    armed: true,
    day: '2026-08-27',
    start_date: '2026-08-27',
    end_date: '2026-08-27',
    speed: 60,
    loop: false,
    clock: {
      sim_now_utc: '2026-08-27T03:45:00Z',
      speed: 60,
      paused: false,
      end_utc: null,
      loop: false,
      completed: false,
    },
  },
} as const

// Helper: override the recorder replay calendar with a populated day
// list so the "Arm replay" button becomes reachable. Returns the
// picked day (today, ISO yyyy-mm-dd).
async function populateReplayCalendar(
  page: import('@playwright/test').Page,
  today: string,
) {
  await page.route('**/localhost:8899/trade/recording/replay/calendar', async (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok',
        days: [
          {
            date: today,
            has_nifty: true,
            has_banknifty: true,
            has_sensex: false,
            nifty_rows: 1200,
            banknifty_rows: 800,
            sensex_rows: 0,
          },
        ],
        underlyings: ['NIFTY', 'BANKNIFTY'],
      }),
    })
  })
}

test.describe('Stock Simulator — render baseline', () => {
  test('mounts all panels without crash or console error', async ({ page }) => {
    const consoleErrors = trackConsoleErrors(page)
    await installBaselineApiMock(page)
    await page.goto('/simulator')

    // Equity picker + live spot panel render (already covered by the
    // equity-picker spec, repeated here so this spec is self-contained
    // when read alone).
    await expect(page.getByTestId('simulator-primary-symbol')).toBeVisible({ timeout: 10_000 })
    await expect(page.getByTestId('live-spot-mode')).toContainText(/LIVE|BROKER|RECENT|CLOSED|REPLAY/)

    expect(await hasErrorBoundary(page)).toBe(false)
    expect(consoleErrors, `console errors: ${consoleErrors.join('\n')}`).toEqual([])
  })
})

test.describe('Stock Simulator — replay arm/disarm', () => {
  test('"Arm replay" flips the live-panel source badge from LIVE to REPLAY', async ({ page }) => {
    // Wiring-drift guard: Simulator.tsx armReplay() → api.startReplay
    // → POST /trade/recording/<day>/replay. The page sets
    // `armedRange` on success, which Simulator.tsx passes as
    // `isReplayArmed={Boolean(armedRange)}` to SimulatorLiveIndexPanel,
    // which uses it to:
    //   - skip the live-only `session_open` poll (line 214)
    //   - send `?replay=1` on /trade/hub/market-data/ticks + spot
    //     (lines 208, 210)
    //   - render the badge as "REPLAY" instead of "LIVE" (line 82)
    // If any of those wires drift (e.g. badge falls back to LIVE because
    // a refactor forgot the isReplayArmed prop), this assertion catches it.
    const consoleErrors = trackConsoleErrors(page)
    await installBaselineApiMock(page)

    const today = new Date().toISOString().slice(0, 10)
    await populateReplayCalendar(page, today)
    // Track the arm POST body for assertion.
    let armPostBody: unknown = null
    await page.route(`**/localhost:8899/trade/recording/${today}/replay`, async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      armPostBody = JSON.parse(route.request().postData() ?? '{}')
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(ARMED_REPLAY_RESPONSE),
      })
    })

    await page.goto('/simulator')
    await expect(page.getByTestId('simulator-primary-symbol')).toBeVisible({ timeout: 10_000 })

    // Wait for the calendar day cell to render (data-testid="replay-day-YYYY-MM-DD").
    const dayCell = page.getByTestId(`replay-day-${today}`)
    await expect(dayCell).toBeVisible({ timeout: 10_000 })

    // Click the day to set replayRange.
    await dayCell.click()

    // Arm replay button is enabled and labelled "Arm replay · <date>".
    const armButton = page.getByRole('button', { name: new RegExp(`^Arm replay · ${today}`) })
    await expect(armButton).toBeEnabled({ timeout: 5_000 })
    await armButton.click()

    // The badge flips to REPLAY once the response arrives.
    await expect(page.getByTestId('live-spot-mode')).toContainText('REPLAY', { timeout: 5_000 })

    // The POST body should have included the speed + optional end_date.
    expect(armPostBody).not.toBeNull()
    const body = armPostBody as Record<string, unknown>
    expect(body.speed).toBeDefined()

    expect(consoleErrors, `console errors: ${consoleErrors.join('\n')}`).toEqual([])
  })

  test('"Stop replay" flips the badge back from REPLAY to LIVE-class', async ({ page }) => {
    // Inverse of the arm test: armed → click Stop → badge reverts.
    // The Stop button text is rendered by SimulatorReplayClock when
    // `armedRange` is non-null.
    const consoleErrors = trackConsoleErrors(page)
    await installBaselineApiMock(page)

    const today = new Date().toISOString().slice(0, 10)
    await populateReplayCalendar(page, today)
    await page.route(`**/localhost:8899/trade/recording/${today}/replay`, async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(ARMED_REPLAY_RESPONSE),
      })
    })
    await page.route('**/localhost:8899/trade/recording/replay/stop', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', replay: { armed: false, clock: null } }),
      })
    })

    await page.goto('/simulator')
    await expect(page.getByTestId('simulator-primary-symbol')).toBeVisible({ timeout: 10_000 })

    // Arm first.
    const dayCell = page.getByTestId(`replay-day-${today}`)
    await expect(dayCell).toBeVisible({ timeout: 10_000 })
    await dayCell.click()
    const armButton = page.getByRole('button', { name: new RegExp(`^Arm replay · ${today}`) })
    await expect(armButton).toBeEnabled({ timeout: 5_000 })
    await armButton.click()
    await expect(page.getByTestId('live-spot-mode')).toContainText('REPLAY', { timeout: 5_000 })

    // The Stop button — SimulatorReplayClock renders it inside the
    // armed branch. Click by text.
    const stopButton = page.getByRole('button', { name: /^Stop/ }).first()
    await expect(stopButton).toBeVisible({ timeout: 5_000 })
    await stopButton.click()

    // Eventually the badge reverts to a non-REPLAY label.
    await expect(page.getByTestId('live-spot-mode')).not.toContainText('REPLAY', { timeout: 10_000 })

    expect(consoleErrors, `console errors: ${consoleErrors.join('\n')}`).toEqual([])
  })
})

test.describe('Stock Simulator — populated replay ticks', () => {
  test('replay-mode ticks populate the chart LTP, not the spot fallback', async ({ page }) => {
    // Type-drift guard: SimulatorLiveIndexPanel lines 219-274 compute
    // `lastTickPrice` from the ticks response, then prefer it over
    // spotRes.spot.ltp when isReplayArmed=true (line 274). If a refactor
    // drops that branch, the LTP in replay mode would equal the spot
    // price (which is NOT what replay data should show), and this test
    // fails by the LTP differing from the spot we returned.
    const consoleErrors = trackConsoleErrors(page)
    await installBaselineApiMock(page)

    const today = new Date().toISOString().slice(0, 10)
    await populateReplayCalendar(page, today)
    await page.route(`**/localhost:8899/trade/recording/${today}/replay`, async (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(ARMED_REPLAY_RESPONSE),
      })
    })
    // Override ticks + spot for replay-mode polls. Spot returns one
    // value, ticks return a clearly different LTP — if the panel
    // mixes them up, the rendered LTP won't match either.
    const replaySpot = { ltp: 24000.0 }
    const replayTickLtp = 24500.5
    await page.route('**/localhost:8899/trade/hub/market-data/ticks**', async (route) => {
      const url = new URL(route.request().url())
      if (url.searchParams.get('replay') !== '1') return route.fallback()
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'ok',
          symbol: 'NIFTY',
          exchange: 'NSE_INDEX',
          source: 'simulator',
          ticks: [
            { ts: '2026-08-27T03:44:00Z', price: replayTickLtp - 5, volume: 100 },
            { ts: '2026-08-27T03:44:30Z', price: replayTickLtp, volume: 150 },
            { ts: '2026-08-27T03:45:00Z', price: replayTickLtp + 3, volume: 200 },
          ],
        }),
      })
    })
    await page.route('**/localhost:8899/trade/hub/market-data/spot**', async (route) => {
      const url = new URL(route.request().url())
      if (url.searchParams.get('replay') !== '1') return route.fallback()
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'ok',
          symbol: 'NIFTY',
          exchange: 'NSE_INDEX',
          spot: { ...replaySpot, source: 'simulator', as_of: '2026-08-27T03:45:00Z' },
          session_open: true,
        }),
      })
    })

    await page.goto('/simulator')
    await expect(page.getByTestId('simulator-primary-symbol')).toBeVisible({ timeout: 10_000 })

    const dayCell = page.getByTestId(`replay-day-${today}`)
    await expect(dayCell).toBeVisible({ timeout: 10_000 })
    await dayCell.click()
    const armButton = page.getByRole('button', { name: new RegExp(`^Arm replay · ${today}`) })
    await expect(armButton).toBeEnabled({ timeout: 5_000 })
    await armButton.click()
    await expect(page.getByTestId('live-spot-mode')).toContainText('REPLAY', { timeout: 5_000 })

    // The LTP should reflect a replay-tick price (24495.5 / 24500.5 /
    // 24503.5), not the spot (24000.0). The component picks the last
    // tick in the array (SimulatorLiveIndexPanel line ~270) which is
    // 24503.5. renderLtp formats with .toLocaleString() which adds a
    // thousands separator.
    const ltpLocator = page.getByTestId('live-spot-ltp')
    await expect(ltpLocator).toContainText('24,503', { timeout: 10_000 })
    // And it must NOT be the spot's value (24000.x).
    await expect(ltpLocator).not.toContainText('24,000')

    expect(consoleErrors, `console errors: ${consoleErrors.join('\n')}`).toEqual([])
  })
})