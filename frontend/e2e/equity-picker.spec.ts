import { expect, test } from '@playwright/test'
import { hasErrorBoundary, installBaselineApiMock, trackConsoleErrors } from './helpers/apiMock'

/**
 * Equity picker selection on /simulator (src/pages/Simulator.tsx). The
 * "Chart symbol" <select data-testid="simulator-primary-symbol"> is the
 * picker actually wired to the live chart/detail panel: changing it drives
 * `primarySymbol`, which is passed straight into
 * `SimulatorLiveIndexPanel` (src/components/simulator/SimulatorLiveIndexPanel.tsx)
 * as `symbol`/`exchange` props. That panel renders the selected symbol name
 * (data-testid="live-spot-mode" area) and its LTP (data-testid="live-spot-ltp"),
 * so selecting an equity there and asserting those update is a real,
 * content-specific check that the selection propagates to the UI — not just
 * that the <select> value changed.
 *
 * The equity options only populate once GET /trade/recording/constituents
 * resolves (src/pages/Simulator.tsx's `nifty50` state), so the baseline
 * mock's canned constituent list (RELIANCE, TCS, HDFCBANK) is what makes an
 * equity option available to pick.
 */

test.describe('Equity picker', () => {
  test('selecting an equity updates the live chart panel', async ({ page }) => {
    const consoleErrors = trackConsoleErrors(page)
    await installBaselineApiMock(page)

    await page.goto('/simulator')

    const picker = page.getByTestId('simulator-primary-symbol')
    await expect(picker).toBeVisible()

    // Defaults to the NIFTY index — confirm the live panel starts there.
    await expect(page.getByTestId('live-spot-mode')).toContainText('NIFTY')

    // Wait for the equity options to populate from the mocked constituents
    // list, then select one.
    await expect(picker.locator('option[value="NSE:RELIANCE"]')).toHaveCount(1, { timeout: 10_000 })
    await picker.selectOption('NSE:RELIANCE')

    // The live panel switches to the newly selected equity.
    await expect(page.getByTestId('live-spot-mode')).toContainText('RELIANCE')
    await expect(page.getByTestId('live-spot-ltp')).toBeVisible()
    await expect(page.getByTestId('live-spot-ltp')).not.toHaveText('—')

    expect(await hasErrorBoundary(page)).toBe(false)
    expect(consoleErrors, `console errors: ${consoleErrors.join('\n')}`).toEqual([])
  })
})
