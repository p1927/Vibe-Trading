import { expect, test } from '@playwright/test'
import { hasErrorBoundary, installBaselineApiMock, trackConsoleErrors } from './helpers/apiMock'

/**
 * Predictions panel render on the /prediction route (src/pages/Prediction.tsx
 * + src/components/prediction/*). This is the regression class backlog item
 * 2026-08-21-vibetrading-prediction-panel-type-drift warns about: a
 * type-drift bug where the panel silently failed to render. The assertions
 * below check for specific rendered content — the controls bar and its
 * horizon options — rather than just "the page didn't crash", plus the
 * shared no-error-boundary / no-console-error baseline every spec makes.
 *
 * Prediction.tsx fans out to ~15 backend endpoints on mount (index
 * prediction, factor history, backtest, track scoreboard, miss analysis,
 * news scenarios, ...) via hooks that all wrap their fetches in try/catch
 * and fall back to null/empty state (see hooks/useIndexPrediction.ts). The
 * shared baseline mock's generic `{status: "ok"}` / typed-empty fallback is
 * enough for those to settle into their idle empty state without throwing.
 */

test.describe('Predictions panel', () => {
  test('renders controls and section content with no crash', async ({ page }) => {
    const consoleErrors = trackConsoleErrors(page)
    await installBaselineApiMock(page)

    await page.goto('/prediction')

    // PredictionControls: heading + horizon-day selector always render
    // regardless of artifact state — a concrete, content-specific assertion
    // (not just "page rendered something").
    await expect(page.getByRole('heading', { name: 'NIFTY 50 Prediction' })).toBeVisible()

    const horizonSelect = page.getByLabel('Prediction horizon')
    await expect(horizonSelect).toBeVisible()
    await expect(horizonSelect.locator('option', { hasText: 'Tactical (2d)' })).toHaveCount(1)
    await expect(horizonSelect.locator('option', { hasText: 'Default (14d)' })).toHaveCount(1)

    // The "Run analysis" control (Play icon button) is always present.
    const runButton = page.getByRole('button', { name: 'Run analysis' })
    await expect(runButton).toBeVisible()

    expect(await hasErrorBoundary(page)).toBe(false)
    expect(consoleErrors, `console errors: ${consoleErrors.join('\n')}`).toEqual([])
  })

  test('scoreboard mode renders without crashing', async ({ page }) => {
    const consoleErrors = trackConsoleErrors(page)
    await installBaselineApiMock(page)

    await page.goto('/prediction?mode=scoreboard')
    await expect(page.getByText(/scoreboard/i).first()).toBeVisible({ timeout: 10_000 })

    expect(await hasErrorBoundary(page)).toBe(false)
    expect(consoleErrors, `console errors: ${consoleErrors.join('\n')}`).toEqual([])
  })
})
