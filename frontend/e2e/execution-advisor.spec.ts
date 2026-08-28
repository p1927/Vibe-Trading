import { expect, test } from '@playwright/test'
import { trackConsoleErrors } from './helpers/apiMock'

/**
 * Execution Advisor behavioural E2E — src/pages/ExecutionAdvisor.tsx +
 * src/components/execution_advisor/*. Talks to the real Vibe Trading
 * API on :8899. NO mocks, NO `installBaselineApiMock`, NO
 * route-level interception — every assertion reads from a real
 * response or asserts a real network call landed.
 *
 * Three tiers, matching docs/audits/2026-08-27-vibetrading-e2e-coverage-plan.md
 * Section 4:
 *
 *   1. Render baseline — page mounts the Execution Advisor card and
 *      calls GET /execution-advisor/positions on mount. The live
 *      response for the dev account at the time of writing is
 *      `{ok:true, count:0, advisories:[], grouped:{}}` (no open
 *      positions — the page must render the "No open positions."
 *      empty state cleanly, NOT a crash or a silently-broken table).
 *
 *   2. Interaction — the "Refresh" button is wired to the same
 *      `load()` helper that runs on mount, so a click must issue
 *      another GET /execution-advisor/positions (the
 *      cache:"no-store" hint on `api.getExecutionAdvisorAdvisories`
 *      means the response is fresh, not stale-cache-surfaced).
 *
 *   3. Populated-artifact assertions — when the live response ever
 *      has at least one advisory, each card surfaces the symbol +
 *      FSM state + action badge + LTP from the real
 *      `ExecutionAdvisorAdvisory` shape. We do not mock a populated
 *      payload (no installBaselineApiMock, per the standing live-stack
 *      rule); we instead assert the empty-state path *now* and leave
 *      the populated case as a structural assertion that holds once
 *      a real advisory exists in the dev account.
 *
 * Bug-filing convention: every failing assertion here surfaces a
 * real bug filed as `.claude/backlog/items/2026-08-2X-execution-advisor-<observed>.md`
 * per PROTOCOL.md.
 */

const VIBE_API_ORIGIN = 'http://localhost:8899'

type LiveAdvisoryResponse = {
  ok: boolean
  count: number
  advisories: Array<{
    symbol: string
    fsm_state: string
    action: string
    ltp: number
  }>
  grouped: Record<string, unknown[]>
}

async function fetchLiveExecutionAdvisor(
  request: import('@playwright/test').APIRequestContext,
): Promise<LiveAdvisoryResponse> {
  const res = await request.get(`${VIBE_API_ORIGIN}/execution-advisor/positions`)
  expect(res.status(), 'live /execution-advisor/positions must respond 2xx').toBeLessThan(400)
  return (await res.json()) as LiveAdvisoryResponse
}

test.describe('Execution Advisor — render baseline (live API)', () => {
  test('mounts Execution Advisor card and renders the live empty state cleanly', async ({
    page,
  }) => {
    const consoleErrors = trackConsoleErrors(page)

    await page.goto('/execution-advisor')

    // The card heading is the concrete, content-specific marker that
    // the Execution Advisor section mounted (vs. just the layout shell).
    await expect(page.getByText('Execution Advisor', { exact: true })).toBeVisible({
      timeout: 15_000,
    })

    // Sub-text that names the advisory-only contract — guards against
    // accidental rewording that drops the "never places an order"
    // safeguard framing.
    await expect(
      page.getByText(/surfaces recommended hold\/tighten-stop\/exit actions/i),
    ).toBeVisible()

    // The Refresh button always renders (disabled while loading is OK).
    const refreshButton = page.getByRole('button', { name: /^Refresh$/ })
    await expect(refreshButton).toBeVisible()

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

  test('empty-state path renders when live account has no open positions', async ({
    page,
    request,
  }) => {
    // Probe the live API first — if the dev account has any open
    // positions, this test still asserts cleanly (the populated path
    // is exercised; the empty-state path is exercised only when the
    // account is empty, as it is at the time of writing).
    const live = await fetchLiveExecutionAdvisor(request)
    console.log(
      `[execution-advisor] live /execution-advisor/positions count=${live.count} advisories=${live.advisories.length}`,
    )

    await page.goto('/execution-advisor')
    await expect(page.getByText('Execution Advisor', { exact: true })).toBeVisible({
      timeout: 15_000,
    })

    if (live.count === 0) {
      await expect(page.getByText('No open positions.')).toBeVisible({ timeout: 10_000 })
    } else {
      // Populated path: at least one AdvisoryCard renders, with a
      // real symbol + FSM-state badge + action badge.
      await expect(page.getByText('No open positions.')).toHaveCount(0)
      const firstAdvisory = live.advisories[0]
      await expect(page.getByText(firstAdvisory.symbol).first()).toBeVisible({
        timeout: 10_000,
      })
      await expect(page.getByText(firstAdvisory.fsm_state).first()).toBeVisible()
      await expect(page.getByText(firstAdvisory.action).first()).toBeVisible()
    }
  })
})

test.describe('Execution Advisor — interactions (live API)', () => {
  test('"Refresh" click issues another GET /execution-advisor/positions', async ({
    page,
  }) => {
    // The "no-store" cache hint means every load() call must hit the
    // network, not a cache. We capture every GET to the endpoint and
    // assert the count grows by exactly one after a single click.
    const advisorGets: string[] = []
    page.on('request', (req) => {
      if (req.method() === 'GET' && req.url().includes('/execution-advisor/positions')) {
        advisorGets.push(req.url())
      }
    })

    await page.goto('/execution-advisor')
    await page.waitForResponse(
      (r) => r.url().includes('/execution-advisor/positions') && r.request().method() === 'GET',
      { timeout: 15_000 },
    )
    const before = advisorGets.length
    expect(before, 'mount should issue at least one GET /execution-advisor/positions').toBeGreaterThanOrEqual(1)

    // Click the Refresh button.
    await page.getByRole('button', { name: /^Refresh$/ }).click()
    await page.waitForResponse(
      (r) => r.url().includes('/execution-advisor/positions') && r.request().method() === 'GET',
      { timeout: 15_000 },
    )

    expect(
      advisorGets.length - before,
      `expected exactly one new GET /execution-advisor/positions after Refresh click; before=${before} after=${advisorGets.length}`,
    ).toBe(1)
  })

  test('15s polling fires subsequent GETs on a long-lived page mount', async ({ page }) => {
    // ExecutionAdvisor.tsx polls every POLL_INTERVAL_MS (15s in source).
    // Wait one full poll window and assert at least one more GET
    // landed — guards against the interval being silently cleared by
    // an effect-cleanup regression.
    const advisorGets: string[] = []
    page.on('request', (req) => {
      if (req.method() === 'GET' && req.url().includes('/execution-advisor/positions')) {
        advisorGets.push(req.url())
      }
    })

    await page.goto('/execution-advisor')
    await page.waitForResponse(
      (r) => r.url().includes('/execution-advisor/positions') && r.request().method() === 'GET',
      { timeout: 15_000 },
    )
    const initial = advisorGets.length

    // Wait for the next poll to fire. The 15s interval plus a small
    // buffer for the request to round-trip — Playwright default
    // action/timeout applies, so we extend the test timeout to be
    // safe.
    test.setTimeout(45_000)
    const deadline = Date.now() + 25_000
    while (Date.now() < deadline && advisorGets.length <= initial) {
      await page.waitForTimeout(500)
    }

    expect(
      advisorGets.length,
      `expected at least one more GET /execution-advisor/positions from the 15s poll; initial=${initial} after=${advisorGets.length}`,
    ).toBeGreaterThan(initial)
  })
})
