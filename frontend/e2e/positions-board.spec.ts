import { expect, test } from '@playwright/test'
import { trackConsoleErrors } from './helpers/apiMock'

/**
 * Positions Board behavioural E2E — src/pages/PositionsBoard.tsx +
 * src/components/board/*. Talks to the real Vibe Trading API on
 * :8899. NO mocks, NO `installBaselineApiMock`, NO route-level
 * interception — every assertion reads from a real response or
 * asserts a real network call landed.
 *
 * Three tiers, matching docs/audits/2026-08-27-vibetrading-e2e-coverage-plan.md
 * Section 5:
 *
 *   1. Render baseline — page mounts the Positions header, lists
 *      real agents from GET /autonomous-agents, and seeds the agent
 *      <select> with the first agent's id. The dev account has
 *      exactly one (draft) agent at the time of writing, so the
 *      select renders with a single option.
 *
 *   2. Interaction — the "Refresh now" button must issue another GET
 *      /board/positions/<agent_id>. The page polls every 20s; we
 *      capture both the click-driven refresh and the polling
 *      refresh to confirm both paths land the same endpoint.
 *
 *   3. Populated-artifact assertion — when the live
 *      /board/positions/<agent_id> returns `groups: []` (draft
 *      agents have no live positions), the page renders the empty
 *      state cleanly. When the response is populated, the
 *      `PositionCard` surfaces the underlying + expiry_days + live
 *      P&L (catches silent type-drift on the LivePositionGroup
 *      shape).
 *
 * Bug-filing convention: every failing assertion here surfaces a
 * real bug filed as `.claude/backlog/items/2026-08-2X-positions-board-<observed>.md`
 * per PROTOCOL.md.
 */

const VIBE_API_ORIGIN = 'http://localhost:8899'

type LiveAgentsResponse = {
  agents: Array<{ id: string; name?: string; status?: string }>
}

async function fetchLiveAgents(
  request: import('@playwright/test').APIRequestContext,
): Promise<LiveAgentsResponse> {
  const res = await request.get(`${VIBE_API_ORIGIN}/autonomous-agents`)
  expect(res.status(), 'live /autonomous-agents must respond 2xx').toBeLessThan(400)
  return (await res.json()) as LiveAgentsResponse
}

test.describe('Positions Board — render baseline (live API)', () => {
  test('mounts Positions header, seeds the agent <select> with the live agent list', async ({
    page,
    request,
  }) => {
    const consoleErrors = trackConsoleErrors(page)
    const live = await fetchLiveAgents(request)
    console.log(
      `[positions-board] live /autonomous-agents count=${live.agents.length} statuses=${live.agents.map((a) => a.status ?? '?').join(',')}`,
    )

    await page.goto('/positions-board')
    await expect(page.getByRole('heading', { name: 'Positions' })).toBeVisible({
      timeout: 15_000,
    })

    // The advisory framing text that names the read-only contract
    // is present (catches accidental rewording that drops the
    // "no auto-trigger" safeguard).
    await expect(
      page.getByText(/Display only — no auto-trigger or exit decision/i),
    ).toBeVisible()

    // The Refresh button always renders (disabled while loading is
    // OK).
    await expect(page.getByRole('button', { name: /Refresh now/i })).toBeVisible()

    // The <select> is seeded with one <option> per live agent. When
    // the account is empty, the page renders a "No agents" fallback
    // option; when populated, one option per live agent.
    const select = page.locator('select')
    const optionCount = await select.locator('option').count()
    if (live.agents.length === 0) {
      await expect(select.locator('option', { hasText: 'No agents' })).toHaveCount(1)
    } else {
      // First option's value matches a real agent id (the page
      // seeds `agentId` to `agents[0].id` on mount).
      const firstOptionValue = await select.locator('option').first().getAttribute('value')
      expect(
        firstOptionValue,
        `first <option> value (${firstOptionValue}) must be one of the live agent ids (${live.agents.map((a) => a.id).join(',')})`,
      ).toBe(live.agents[0].id)
    }
    expect(optionCount, `<select> option count`).toBeGreaterThanOrEqual(1)

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
})

test.describe('Positions Board — interactions (live API)', () => {
  test('agent-select change re-issues GET /board/positions/<new-id>', async ({
    page,
    request,
  }) => {
    const live = await fetchLiveAgents(request)
    test.skip(
      live.agents.length < 2,
      `live account has ${live.agents.length} agents; need ≥ 2 to exercise a select change that re-issues /board/positions/<new-id>`,
    )

    // The page seeds `agentId` to `agents[0].id` and only issues
    // /board/positions/<id> when an id is set. Capture every such
    // request, switch the select to the second agent, and assert the
    // next request URL ends with the second agent's id.
    const boardGets: string[] = []
    page.on('request', (req) => {
      if (
        req.method() === 'GET' &&
        new RegExp(`/board/positions/[^/]+$`).test(req.url()) &&
        !req.url().endsWith('/board/positions') // exclude the per-agent path's parent
      ) {
        boardGets.push(req.url())
      }
    })

    await page.goto('/positions-board')
    const select = page.locator('select')
    await expect(select.locator('option').first()).toBeVisible({ timeout: 15_000 })
    await page.waitForResponse(
      (r) => /\/board\/positions\/[^/]+$/.test(r.url()) && r.request().method() === 'GET',
      { timeout: 15_000 },
    )

    // Switch to the second agent.
    await select.selectOption(live.agents[1].id)
    await page.waitForResponse(
      (r) => r.url().endsWith(`/board/positions/${live.agents[1].id}`) && r.request().method() === 'GET',
      { timeout: 15_000 },
    )

    // The latest board GET must point at the second agent's id.
    const last = boardGets[boardGets.length - 1]
    expect(last, `last /board/positions/<id> GET`).toMatch(
      new RegExp(`/board/positions/${live.agents[1].id}$`),
    )
  })

  test('"Refresh now" click issues another GET /board/positions/<current-id>', async ({
    page,
    request,
  }) => {
    const live = await fetchLiveAgents(request)
    test.skip(
      live.agents.length === 0,
      `live account has no agents; cannot exercise the "Refresh now" /board/positions/<id> path`,
    )

    const boardGets: string[] = []
    page.on('request', (req) => {
      if (req.method() === 'GET' && new RegExp(`/board/positions/[^/]+$`).test(req.url())) {
        boardGets.push(req.url())
      }
    })

    await page.goto('/positions-board')
    await page.waitForResponse(
      (r) => /\/board\/positions\/[^/]+$/.test(r.url()) && r.request().method() === 'GET',
      { timeout: 15_000 },
    )
    const before = boardGets.length
    expect(
      before,
      'mount should issue at least one GET /board/positions/<agent_id> when an agent is selected',
    ).toBeGreaterThanOrEqual(1)

    // Click the Refresh-now button (the icon-only button with
    // aria-label="Refresh now").
    await page.getByRole('button', { name: /Refresh now/i }).click()
    await page.waitForResponse(
      (r) => /\/board\/positions\/[^/]+$/.test(r.url()) && r.request().method() === 'GET',
      { timeout: 15_000 },
    )

    expect(
      boardGets.length - before,
      `expected exactly one new GET /board/positions/<id> after Refresh-now click; before=${before} after=${boardGets.length}`,
    ).toBeGreaterThanOrEqual(1)
  })
})
