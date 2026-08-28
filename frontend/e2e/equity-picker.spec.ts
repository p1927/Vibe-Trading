import { expect, test } from '@playwright/test'
import { hasErrorBoundary, trackConsoleErrors } from './helpers/apiMock'

/**
 * Equity picker selection on /simulator (src/pages/Simulator.tsx). Talks to
 * the real Vibe Trading API on :8899. NO mocks, NO installBaselineApiMock,
 * NO route-level interception — every assertion reads from a real response.
 *
 * The "Chart symbol" <select data-testid="simulator-primary-symbol"> is the
 * picker actually wired to the live chart/detail panel: changing it drives
 * `primarySymbol`, which is passed straight into
 * `SimulatorLiveIndexPanel` (src/components/simulator/SimulatorLiveIndexPanel.tsx)
 * as `symbol`/`exchange` props. That panel renders the selected symbol name
 * (data-testid="live-spot-mode" area) and its LTP (data-testid="live-spot-ltp"),
 * so selecting an equity there and asserting those update is a real,
 * content-specific check that the selection propagates to the UI — not just
 * that the <select> value changed.
 *
 * The equity options only populate once the live GET /trade/recording/constituents
 * resolves (src/pages/Simulator.tsx's `nifty50` state) — RELIANCE is a real,
 * always-present NIFTY 50 constituent, so it's used as the picked symbol
 * rather than a mocked/synthetic one.
 *
 * Note: `formatLtp()` (SimulatorLiveIndexPanel.tsx) renders "—" only for
 * null/non-finite LTP, and "0" for a real-but-zero quote (e.g. outside
 * market hours or a broker/feed gap) — so `not.toHaveText('—')` still holds
 * even when the live feed currently has nothing better than a 0 quote for
 * RELIANCE (confirmed live: /trade/hub/market-data/spot?symbol=RELIANCE&exchange=NSE
 * returned `spot.ltp: 0.0` with `session_open: false` when this spec was
 * rewritten — a real off-hours/no-feed state, not a bug).
 */

test.describe('Equity picker', () => {
  test('selecting an equity updates the live chart panel', async ({ page }) => {
    const consoleErrors = trackConsoleErrors(page)

    await page.goto('/simulator')

    const picker = page.getByTestId('simulator-primary-symbol')
    await expect(picker).toBeVisible()

    // Defaults to the NIFTY index — confirm the live panel starts there.
    await expect(page.getByTestId('live-spot-mode')).toContainText('NIFTY')

    // Wait for the equity options to populate from the live constituents
    // list, then select a real one.
    await expect(picker.locator('option[value="NSE:RELIANCE"]')).toHaveCount(1, { timeout: 10_000 })
    await picker.selectOption('NSE:RELIANCE')

    // The live panel switches to the newly selected equity. SimulatorLiveIndexPanel
    // polls each visible symbol's spot on an interval rather than fetching
    // on-select synchronously (confirmed live: selecting RELIANCE fires its
    // spot fetch only after the panel's existing NIFTY/BANKNIFTY/SENSEX/
    // GIFTNIFTY poll cycle finishes, ~9-10s after selection) — give it real
    // headroom rather than the default 5s assertion timeout.
    await expect(page.getByTestId('live-spot-mode')).toContainText('RELIANCE')
    await expect(page.getByTestId('live-spot-ltp')).toBeVisible()
    await expect(page.getByTestId('live-spot-ltp')).not.toHaveText('—', { timeout: 15_000 })

    expect(await hasErrorBoundary(page)).toBe(false)
    expect(consoleErrors, `console errors: ${consoleErrors.join('\n')}`).toEqual([])
  })
})
