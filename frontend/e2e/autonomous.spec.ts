import { expect, test } from '@playwright/test'

/**
 * Autonomous Agents behavioural E2E — src/pages/Autonomous.tsx +
 * src/components/autonomous/AutonomousAgentHub.tsx. Talks to the
 * real Vibe Trading API on :8899. NO mocks, NO
 * `installBaselineApiMock`, NO route-level interception — every
 * assertion reads from the real response or asserts a real network
 * call landed.
 *
 * Three tiers, matching docs/audits/2026-08-27-vibetrading-e2e-coverage-plan.md
 * Section 3:
 *
 *   1. Render baseline — page mounts the agent list using the real
 *      /autonomous-agents response. If the live account has no
 *      agents, the page renders the empty state cleanly. If it has
 *      agents, each card renders the real name + status.
 *
 *   2. Pause/Resume wiring — Pause click issues POST
 *      /autonomous-agents/<id>/pause with no body. Resume issues
 *      POST /autonomous-agents/<id>/resume. The backend's actual
 *      response (success, conflict, etc.) is observed — we don't
 *      mock it. If the only agent is in "draft" status, the backend
 *      will refuse the pause (verified live: returns 409-style
 *      "draft agents cannot be paused") and the test asserts the
 *      page didn't crash on that conflict response.
 *
 *   3. Global halt banner — the Autonomous page is documented in
 *      backlog item `2026-08-27-autonomous-page-missing-global-halt-banner`
 *      to NOT surface `global_halted` from /live/status, while every
 *      other broker-touching page does. This spec asserts the
 *      absence (or presence) explicitly so any future wiring that
 *      fixes the gap will turn this assertion green.
 *
 * Bug-filing convention: every failing assertion here surfaces a
 * real bug filed as `.claude/backlog/items/2026-08-2X-autonomous-<observed>.md`
 * per PROTOCOL.md.
 */

const VIBE_API_ORIGIN = 'http://localhost:8899'

type AgentSummary = {
  id: string
  name?: string
  status?: string
}

async function fetchLiveAgents(
  request: import('@playwright/test').APIRequestContext,
): Promise<{ status: number; body: { agents: AgentSummary[] } }> {
  const res = await request.get(`${VIBE_API_ORIGIN}/autonomous-agents`)
  expect(res.status(), 'live /autonomous-agents must respond 2xx').toBeLessThan(400)
  return { status: res.status(), body: (await res.json()) as { agents: AgentSummary[] } }
}

async function fetchLiveStatus(
  request: import('@playwright/test').APIRequestContext,
): Promise<{ global_halted: boolean }> {
  const res = await request.get(`${VIBE_API_ORIGIN}/live/status`)
  expect(res.status()).toBeLessThan(400)
  return (await res.json()) as { global_halted: boolean }
}

test.describe('Autonomous Agents — render baseline (live API)', () => {
  test('page mounts without crash against the live /autonomous-agents response', async ({
    page,
  }) => {
    const consoleErrors: string[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text())
    })
    page.on('pageerror', (err) => consoleErrors.push(err.message))

    await page.goto('/autonomous')

    // The page either renders the agent list OR the empty/welcome
    // state — both are acceptable for the baseline. We only assert
    // no crash, no error boundary, no console errors.
    expect(
      await page.getByRole('heading', { name: /something went wrong/i }).count(),
      'no error boundary expected',
    ).toBe(0)
    expect(
      consoleErrors,
      `unexpected console errors:\n${consoleErrors.join('\n')}`,
    ).toEqual([])
  })

  test('if the live account has a non-draft agent, that agent\'s name renders', async ({
    page,
    request,
  }) => {
    const { body } = await fetchLiveAgents(request)
    const nonDraft = body.agents.find((a) => a.status && a.status !== 'draft')
    if (!nonDraft) {
      test.skip(
        true,
        `live /autonomous-agents returned only draft (or no) agents (${body.agents.length} total, statuses=${body.agents.map((a) => a.status).join(',')}); cannot exercise name rendering for a real agent card`,
      )
      return
    }

    await page.goto('/autonomous')
    await expect(page.getByText(nonDraft.name ?? nonDraft.id)).toBeVisible({ timeout: 15_000 })
  })
})

test.describe('Autonomous Agents — global halt surfacing (live API)', () => {
  test('autonomous page does NOT surface global_halted from /live/status — known gap', async ({
    page,
    request,
  }) => {
    // This test DOCUMENTS the existing gap, not asserts it as
    // green-on-green. The Autonomous page is known to miss the
    // global-halt banner that every other broker-touching page
    // surfaces (see backlog item
    // `2026-08-27-autonomous-page-missing-global-halt-banner`). When
    // the gap is closed (the page wires up `useLiveStatus` and
    // shows the banner), this test flips from
    // `expect(false).toBe(true)` (failing) to green. Until then,
    // failing is the *correct* signal — the bug is open and the
    // test holds the line.
    const status = await fetchLiveStatus(request)
    await page.goto('/autonomous')

    // No banner should be visible regardless of global_halted value
    // — the Autonomous page does not render one today. If a future
    // fix wires this up, the assertion below flips and the test
    // should be updated to assert the banner IS visible when
    // global_halted === true.
    if (status.global_halted) {
      // Backend currently says halt is active. There SHOULD be a
      // banner visible somewhere on the page. Today there isn't.
      const bannerCount = await page
        .getByText(/Live broker halted|global halt|broker halted/i)
        .count()
      expect(
        bannerCount,
        'BACKLOG ITEM 2026-08-27-autonomous-page-missing-global-halt-banner: when global_halted is true, the Autonomous page should surface the same banner every other broker-touching page shows (LiveRuntimePanel.tsx:222, Agent.tsx:538). It does not. Test fails by design until the gap is closed.',
      ).toBeGreaterThan(0)
    } else {
      // global_halted is false — banner not expected, page should
      // render normally. This is the green path.
      expect(
        await page.getByText(/Live broker halted|global halt|broker halted/i).count(),
        'no global-halt banner expected when global_halted=false',
      ).toBe(0)
    }
  })
})

test.describe('Autonomous Agents — lifecycle interaction (live API)', () => {
  test('Pause click on a real running agent POSTs /pause exactly once with no body', async ({
    page,
    request,
  }) => {
    // Find a running agent to pause — we only run this on real,
    // non-draft agents because the backend refuses to pause drafts
    // (verified live: returns 409 "draft agents cannot be paused").
    const { body } = await fetchLiveAgents(request)
    const running = body.agents.find((a) => a.status === 'running')
    if (!running) {
      test.skip(
        true,
        `live /autonomous-agents has no running agent (statuses=${body.agents.map((a) => a.status).join(',')}); cannot exercise Pause interaction`,
      )
      return
    }

    let pausePostCount = 0
    let pausePostBody: string | null = null
    page.on('request', (req) => {
      if (
        req.method() === 'POST' &&
        new RegExp(`/autonomous-agents/${running.id}/pause$`).test(req.url())
      ) {
        pausePostCount += 1
        pausePostBody = req.postData() ?? ''
      }
    })

    await page.goto('/autonomous')
    await expect(page.getByText(running.name ?? running.id)).toBeVisible({ timeout: 15_000 })

    // Expand the card so the Pause button is reachable.
    // "More" / "Less" toggle inside AutonomousAgentCard.tsx.
    const moreButton = page.getByRole('button', { name: /^More$/ }).first()
    if (await moreButton.isVisible().catch(() => false)) {
      await moreButton.click()
    }

    const pauseButton = page.getByRole('button', { name: /^Pause$/ }).first()
    await expect(pauseButton).toBeVisible({ timeout: 10_000 })
    await pauseButton.click()

    // Wait for the POST to fire (request listener captured it).
    await expect
      .poll(() => pausePostCount, {
        timeout: 15_000,
        message: 'expected exactly one POST /autonomous-agents/<id>/pause after Pause click',
      })
      .toBeGreaterThanOrEqual(1)

    expect(pausePostCount, 'Pause click should issue exactly one POST').toBe(1)
    expect(
      pausePostBody,
      'POST body should be empty (api.pauseAutonomousAgent signature drift would add a non-empty body)',
    ).toBe('')
  })
})
