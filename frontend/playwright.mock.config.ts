import { defineConfig, devices } from '@playwright/test'

/**
 * Headless, stack-free run of e2e/prediction.spec.ts: serves the app with vite.e2e.config.ts (no proxy,
 * ephemeral port 49173, own cacheDir) and the spec answers every fetch/xhr/SSE with page.route, so nothing
 * here talks to a real service and no stack is needed.
 * Run: `npx playwright test -c playwright.mock.config.ts`.
 */
const PORT = Number(process.env.E2E_VITE_PORT ?? 49173)

export default defineConfig({
  testDir: './e2e',
  testMatch: /prediction\.spec\.ts/,
  fullyParallel: true,
  reporter: [['list']],
  outputDir: '.e2e-cache/results',
  use: { baseURL: `http://127.0.0.1:${PORT}`, screenshot: 'only-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `npx vite --config vite.e2e.config.ts --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: false,
    timeout: 120 * 1000,
    env: { E2E_VITE_PORT: String(PORT) },
  },
})
