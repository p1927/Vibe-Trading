import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright configuration for E2E testing.
 * Mirrors openalgo/frontend/playwright.config.ts's shape, but scoped to
 * vibetrading's dev port and Chromium only (this repo's CI patterns favor
 * "Chromium only for speed" — see openalgo's own ci.yml comment on its
 * frontend-e2e job).
 * See https://playwright.dev/docs/test-configuration
 */
export default defineConfig({
  testDir: './e2e',
  /* Run tests in files in parallel */
  fullyParallel: true,
  /* Fail the build on CI if you accidentally left test.only in the source code */
  forbidOnly: !!process.env.CI,
  /* Retry on CI only */
  retries: process.env.CI ? 2 : 0,
  /* Opt out of parallel tests on CI */
  workers: process.env.CI ? 1 : undefined,
  /* Reporter to use */
  reporter: 'html',
  /* Shared settings for all the projects below */
  use: {
    /* Base URL to use in actions like `await page.goto('/')` */
    baseURL: 'http://localhost:5899',
    /* Collect trace when retrying the failed test */
    trace: 'on-first-retry',
    /* Screenshot on failure */
    screenshot: 'only-on-failure',
  },

  /* Chromium only for speed — no live backend to mock across every engine,
   * and this repo's CI patterns already favor a single-browser E2E job. */
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  /* Run the Vite dev server before starting the tests. */
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5899',
    reuseExistingServer: !process.env.CI,
    timeout: 120 * 1000,
  },
})
