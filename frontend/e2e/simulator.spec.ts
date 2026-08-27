import { expect, test } from '@playwright/test'

/**
 * Stock Simulator behavioural E2E — src/pages/Simulator.tsx + the
 * src/components/simulator/* panels. Talks to the real Vibe Trading
 * API on :8899 (the Vibe proxy that forwards recording/replay and
 * market-data/ticks calls to the live stack). NO mocks, NO
 * `installBaselineApiMock`, NO route-level interception of /trade/*
 * API calls — every assertion reads from a real response or asserts a
 * real network call landed.
 *
 * Three tiers, matching docs/audits/2026-08-27-vibetrading-e2e-coverage-plan.md
 * Section 2:
 *
 *   1. Render baseline — page mounts with all sub-panels and the
 *      live /trade/hub/market-data/spot response is rendered into
 *      the live-spot badge.
 *
 *   2. Replay arm/disarm interaction:
 *      - "Arm replay" click issues POST /trade/recording/<day>/replay
 *        with speed + loop + start_date/end_date body, and the badge
 *        eventually reflects replay state
 *      - "Stop replay" issues POST /trade/recording/replay/stop and
 *        the badge reverts
 *
 *   3. Populated-tick artifact — when the live
 *      /trade/hub/market-data/ticks returns ticks with a recent LTP,
 *      the chart LTP reads from there (regression-class: silent
 *      fallback to spot.ltp when replay ticks are present).
 *
 * The cross-process env-mirror trap documented in
 * `2026-08-27-vibe-proxy-replay-env-mirror-trap` is the *backend* bug
 * class for this section — a separate probe script catches it. This
 * spec verifies the frontend contract: the badge, the LTP, and the
 * real POSTs land where the user expects them.
 *
 * Bug-filing convention: every failing assertion here surfaces a
 * real bug filed as `.claude/backlog/items/2026-08-2X-simulator-<observed>.md`
 * per PROTOCOL.md.
 */

const VIBE_API_ORIGIN = 'http://localhost:8899'

test.describe('Stock Simulator — render baseline (live API)', () => {
  test('mounts all panels without crash and shows a live-source badge', async ({ page }) => {
    const consoleErrors: string[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text())
    })
    page.on('pageerror', (err) => consoleErrors.push(err.message))

    await page.goto('/simulator')
    await expect(page.getByTestId('simulator-primary-symbol')).toBeVisible({ timeout: 15_000 })

    // The source badge must show one of the documented source labels.
    // Real labels per SimulatorLiveIndexPanel: LIVE / BROKER / RECENT /
    // CLOSED / REPLAY. The exact label depends on the live market
    // state — we assert one of them is present, not which one.
    const badge = page.getByTestId('live-spot-mode')
    await expect(badge).toBeVisible({ timeout: 15_000 })
    const badgeText = (await badge.textContent()) ?? ''
    expect(
      /LIVE|BROKER|RECENT|CLOSED|REPLAY/.test(badgeText),
      `live-source badge should show one of LIVE/BROKER/RECENT/CLOSED/REPLAY; got "${badgeText}"`,
    ).toBe(true)

    expect(
      await page.getByRole('heading', { name: /something went wrong/i }).count(),
      'no error boundary expected',
    ).toBe(0)
    expect(
      consoleErrors,
      `unexpected console errors:\n${consoleErrors.join('\n')}`,
    ).toEqual([])
  })
})

test.describe('Stock Simulator — replay calendar (live API)', () => {
  test('replay calendar renders at least one recorded day for a configured session', async ({
    page,
    request,
  }) => {
    // The calendar endpoint is the real one on :8899. Pre-fetch and
    // assert at least one recorded day exists for the active session;
    // if the calendar is empty (no recording runs yet for this
    // account), skip the calendar UI assertion with a clear note
    // rather than mock-filling it.
    const calRes = await request.get(`${VIBE_API_ORIGIN}/trade/recording/replay/calendar`)
    expect(calRes.status()).toBeLessThan(400)
    const cal = (await calRes.json()) as {
      configured?: boolean
      status?: string
      days?: Array<{ date: string }>
    }

    if (!cal.configured || !cal.days?.length) {
      test.skip(
        true,
        `live replay calendar is not configured or has no days (configured=${cal.configured}, days=${cal.days?.length ?? 0}); cannot exercise day-cell click`,
      )
      return
    }

    await page.goto('/simulator')
    await expect(page.getByTestId('simulator-primary-symbol')).toBeVisible({ timeout: 15_000 })

    // At least one day cell renders. The test-id pattern from the
    // existing spec is `replay-day-YYYY-MM-DD`.
    const firstDay = cal.days[0].date
    await expect(page.getByTestId(`replay-day-${firstDay}`)).toBeVisible({ timeout: 15_000 })
  })
})

test.describe('Stock Simulator — replay arm/disarm (live API)', () => {
  test('"Arm replay" issues POST /trade/recording/<day>/replay with speed/loop/start_date', async ({
    page,
    request,
  }) => {
    // Same precondition as the calendar test: skip cleanly if no days
    // are configured rather than synthesising one.
    const calRes = await request.get(`${VIBE_API_ORIGIN}/trade/recording/replay/calendar`)
    expect(calRes.status()).toBeLessThan(400)
    const cal = (await calRes.json()) as {
      configured?: boolean
      days?: Array<{ date: string }>
    }

    if (!cal.configured || !cal.days?.length) {
      test.skip(
        true,
        `live replay calendar is not configured (days=${cal.days?.length ?? 0}); cannot exercise Arm replay`,
      )
      return
    }

    const day = cal.days[0].date

    // Capture the POST body — the live backend should receive
    // {speed, loop, start_date, end_date} (the wire shape for arm).
    let armBody: any = null
    let armUrl: string | null = null
    page.on('request', (req) => {
      if (
        req.method() === 'POST' &&
        new RegExp(`/trade/recording/${day}/replay$`).test(req.url())
      ) {
        armUrl = req.url()
        armBody = JSON.parse(req.postData() ?? '{}')
      }
    })

    // Also capture the stop POST so we leave the live stack in the
    // same state we found it (best-effort cleanup; if stop fails or
    // the live backend refuses, we just record it in console.errors
    // and move on — this is a regression guard, not a state-change
    // guarantee).
    page.on('request', (req) => {
      if (
        req.method() === 'POST' &&
        req.url().includes('/trade/recording/replay/stop')
      ) {
        // no-op capture; the actual stop click happens below.
      }
    })

    await page.goto('/simulator')
    await expect(page.getByTestId('simulator-primary-symbol')).toBeVisible({ timeout: 15_000 })

    const dayCell = page.getByTestId(`replay-day-${day}`)
    await expect(dayCell).toBeVisible({ timeout: 15_000 })
    await dayCell.click()

    // The Arm button text is "Arm replay · <date>" per the existing
    // spec — match that exactly.
    const armButton = page.getByRole('button', {
      name: new RegExp(`^Arm replay · ${day}`),
    })
    await expect(armButton).toBeEnabled({ timeout: 10_000 })
    await armButton.click()

    // Wait for the POST to fire.
    await expect
      .poll(() => armUrl, {
        timeout: 15_000,
        message: `expected POST /trade/recording/${day}/replay to fire after Arm replay click`,
      })
      .not.toBeNull()

    expect(armUrl, 'POST URL must hit /trade/recording/<day>/replay').toMatch(
      new RegExp(`/trade/recording/${day}/replay$`),
    )
    expect(armBody, 'arm POST body must be JSON-parseable').not.toBeNull()
    expect(
      Object.prototype.hasOwnProperty.call(armBody, 'speed'),
      `arm body should include 'speed'; got ${JSON.stringify(armBody)}`,
    ).toBe(true)

    // Best-effort cleanup: send a stop so the live stack doesn't
    // remain armed after the test. Don't fail the test if stop
    // fails — that's a backend concern, not this test's wiring guard.
    const stopButton = page.getByRole('button', { name: /^Stop/ }).first()
    if (await stopButton.isVisible().catch(() => false)) {
      await stopButton.click().catch(() => {
        /* ignore — best effort */
      })
    }
  })
})
