import { expect, test } from '@playwright/test'
import { hasErrorBoundary, installBaselineApiMock, trackConsoleErrors } from './helpers/apiMock'

/**
 * Chat send/receive on the /agent route (src/pages/Agent.tsx).
 *
 * This page talks to a live agent/LLM backend over SSE (see src/lib/api.ts's
 * `sseUrl` -> GET /sessions/:id/events, and src/hooks/useSSE.ts which opens a
 * native `EventSource`). We mock every network call at the boundary — POST
 * /sessions (session create), POST /sessions/:id/messages (send), and the
 * GET /sessions/:id/events SSE stream itself — so the test exercises the
 * real composer/store/render pipeline without depending on a live backend.
 */

const SESSION_ID = 'e2e-test-session-1'
const ASSISTANT_REPLY = 'Mocked assistant reply for e2e testing.'

function sseBody(): string {
  // Mirrors the shape Agent.tsx's setupSSE handlers expect (see the
  // `connect(api.sseUrl(...), {...})` handler map): message.received echoes
  // the user turn (already added optimistically by runPrompt, so this must
  // be deduped — Agent.tsx's optimisticDuplicate check does that by
  // requestText+timestamp, so we omit message.received entirely here and
  // just stream text + complete, which is sufficient and simpler), a few
  // text_delta chunks, then attempt.completed with a `summary` (Agent.tsx
  // prefers `d.summary` over the accumulated streamed text for the final
  // answer message).
  const events: string[] = []
  const textDeltaChunks = [ASSISTANT_REPLY.slice(0, 10), ASSISTANT_REPLY.slice(10)]
  for (const chunk of textDeltaChunks) {
    events.push(
      `event: text_delta\ndata: ${JSON.stringify({ delta: chunk, attempt_id: 'attempt-1' })}\n\n`,
    )
  }
  events.push(
    `event: attempt.completed\ndata: ${JSON.stringify({
      attempt_id: 'attempt-1',
      summary: ASSISTANT_REPLY,
      elapsed_ms: 42,
    })}\n\n`,
  )
  return events.join('')
}

test.describe('Chat send/receive', () => {
  test('composer accepts input, message renders, mocked reply renders', async ({ page }) => {
    const consoleErrors = trackConsoleErrors(page)
    await installBaselineApiMock(page)

    await page.route('**/localhost:8899/sessions', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ session_id: SESSION_ID, title: '', status: 'active' }),
      })
    })

    await page.route(`**/localhost:8899/sessions/${SESSION_ID}/messages`, async (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ message_id: 'msg-1', attempt_id: 'attempt-1' }),
      })
    })

    // `route.fulfill` sends one complete response body and then the
    // connection closes — a real SSE endpoint instead stays open
    // indefinitely. The browser's native `EventSource` treats that close as
    // a dropped connection (fires `onerror`) and auto-reconnects
    // (src/hooks/useSSE.ts's `scheduleReconnect`), which would re-request
    // this same route and replay the exact same events a second time. Only
    // answer the *first* connection with the scripted event stream; every
    // reconnect after that gets an empty stream, so the mock doesn't cause
    // its own infinite replay loop.
    let sseDelivered = false
    await page.route(`**/localhost:8899/sessions/${SESSION_ID}/events*`, async (route) => {
      const body = sseDelivered ? '' : sseBody()
      sseDelivered = true
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body,
      })
    })

    await page.goto('/agent')

    const composer = page.getByLabel(/message/i).or(page.locator('textarea'))
    await expect(composer.first()).toBeVisible()

    const prompt = 'What is the current market regime?'
    await composer.first().fill(prompt)

    const sendButton = page.getByRole('button', { name: /send/i })
    await expect(sendButton).toBeEnabled()
    await sendButton.click()

    // The user's own message renders immediately (optimistic add in runPrompt).
    await expect(page.getByText(prompt)).toBeVisible()

    // The mocked assistant reply streams in and renders once attempt.completed
    // fires (Agent.tsx uses `d.summary` as the final answer content).
    await expect(page.getByText(ASSISTANT_REPLY)).toBeVisible({ timeout: 15_000 })

    expect(await hasErrorBoundary(page)).toBe(false)
    // A single expected "404" resource-load entry is benign here: Agent.tsx's
    // `loadGoalSnapshot` fetches `GET /sessions/:id/goal` on mount and treats
    // a 404 as "no research goal attached to this session" (its own,
    // already-exercised empty-state path — see the try/catch around
    // `api.getGoal` in src/pages/Agent.tsx). The browser logs any non-2xx
    // response as a "Failed to load resource" console entry regardless of
    // how gracefully the app handles it, so that one entry is expected and
    // filtered; anything else still fails the baseline check.
    const unexpectedConsoleErrors = consoleErrors.filter(
      (msg) => !msg.includes('404 (Not Found)'),
    )
    expect(unexpectedConsoleErrors, `console errors: ${unexpectedConsoleErrors.join('\n')}`).toEqual([])
  })
})
