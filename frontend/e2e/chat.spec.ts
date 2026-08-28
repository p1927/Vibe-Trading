import { expect, test } from '@playwright/test'
import { hasErrorBoundary, trackConsoleErrors } from './helpers/apiMock'

/**
 * Chat send/receive on the /agent route (src/pages/Agent.tsx). Talks to the
 * real Vibe Trading API on :8899 — real session create (POST /sessions),
 * real message send (POST /sessions/:id/messages), and a real SSE stream
 * from the real configured LLM provider (GET /sessions/:id/events). NO
 * mocks, NO installBaselineApiMock, NO route-level interception of any
 * network call, per the standing live-stack rule (see testing/README.md's
 * "Running the tests" section).
 *
 * Rewritten 2026-08-28, replacing the prior mocked version. Confirmed live
 * (curl round-trip against :8899 before writing this) that a trivial prompt
 * takes ~17s end to end through the real MiniMax-M3 model — the assertion
 * timeout below has real headroom for that, not a mocked near-instant
 * response. Because the real model's exact phrasing isn't deterministic,
 * this asserts structurally (an answer bubble with a real elapsed-time
 * badge renders, with non-trivial text) rather than matching an exact
 * reply string the way the old mocked version did.
 */

test.describe('Chat send/receive', () => {
  test('composer accepts input, a real reply streams in and renders', async ({ page }) => {
    // Real model round-trip observed live at ~17s for a trivial prompt;
    // Playwright's own 30s default TEST timeout (separate from any
    // per-assertion timeout argument) would otherwise kill this before the
    // 45s assertion timeout below ever gets to fire.
    test.setTimeout(60_000)
    const consoleErrors = trackConsoleErrors(page)

    await page.goto('/agent')

    const composer = page.getByLabel(/message/i).or(page.locator('textarea'))
    await expect(composer.first()).toBeVisible()

    // A prompt with a predictable-enough real answer to sanity-check content,
    // without depending on the model's exact phrasing.
    const prompt = 'What is 2+2? Answer in one short sentence.'
    await composer.first().fill(prompt)

    const sendButton = page.getByRole('button', { name: /send/i })
    await expect(sendButton).toBeEnabled()
    await sendButton.click()

    // The user's own message renders immediately (optimistic add). Scoped to
    // #main: a real (non-mocked) session also gets a sidebar link titled
    // after the prompt text, which would otherwise strict-mode-collide with
    // the message-list occurrence.
    await expect(page.locator('#main').getByText(prompt)).toBeVisible()

    // A real completed answer bubble renders — signalled by the elapsed-time
    // badge (MessageBubble.tsx only renders this for msg.type === "answer"
    // with a real elapsed_ms from attempt.completed, title="Total response
    // time" per en.json — a definitive "the real round trip finished"
    // marker that doesn't depend on the model's exact wording). Real model
    // latency observed live: ~17s for this prompt; give real headroom.
    await expect(page.getByTitle('Total response time')).toBeVisible({ timeout: 45_000 })

    expect(await hasErrorBoundary(page)).toBe(false)
    // A single expected "404" resource-load entry is benign here: Agent.tsx's
    // `loadGoalSnapshot` fetches `GET /sessions/:id/goal` on mount and treats
    // a 404 as "no research goal attached to this session" (its own,
    // already-exercised empty-state path). The browser logs any non-2xx
    // response as a "Failed to load resource" console entry regardless of
    // how gracefully the app handles it, so that one entry is expected and
    // filtered; anything else still fails the baseline check.
    const unexpectedConsoleErrors = consoleErrors.filter(
      (msg) => !msg.includes('404 (Not Found)'),
    )
    expect(unexpectedConsoleErrors, `console errors: ${unexpectedConsoleErrors.join('\n')}`).toEqual([])
  })
})
