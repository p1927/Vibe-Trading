import { expect, test } from '@playwright/test'
import { trackConsoleErrors } from './helpers/apiMock'

/**
 * Knowledge Engine behavioural E2E — src/pages/KnowledgeEngine.tsx +
 * vibetrading/agent/src/api/knowledge_engine_routes.py
 * (GET /knowledge/{wiki,wiki/{slug},strategies,news-derived-strategies,
 *  track-record,factors,factors/{factor_key}}).
 *
 * Talks to the real Vibe Trading API on :8899. NO mocks, NO
 * `installBaselineApiMock`, NO route-level interception — every
 * assertion reads from a real response or asserts a real network
 * call landed.
 *
 * Three tiers, matching docs/audits/2026-08-27-vibetrading-e2e-coverage-plan.md
 * Sections 7..12 (light sections) — Knowledge Engine was user-selected
 * 2026-08-28 from the Section 7..12 list (the page has substantive
 * backend hooks despite the plan's "light" label, so this spec exercises
 * the same three-tier pattern as Sections 1..6):
 *
 *   1. Render baseline — page mounts with the real
 *      GET /knowledge/wiki response. The dev wiki is populated at the
 *      time of writing (count=10+ entries, score-bearing, tags present),
 *      so the page renders the populated wiki list. The 3 tabs
 *      (Wiki / Strategy Catalog / News-Derived Concepts) all render;
 *      Wiki is the default.
 *
 *   2. Interaction — clicking the "Strategy Catalog" tab fires
 *      GET /knowledge/strategies and renders the populated strategy
 *      cards. Typing in the "Market view" filter input narrows the
 *      request to ?market_view=bullish and the response stays 2xx.
 *      Clicking the "News-Derived Concepts" tab fires
 *      GET /knowledge/news-derived-strategies and renders the
 *      empty-state ("No news-derived concepts yet…") because the live
 *      dev DB has zero verified concepts at the time of writing.
 *
 *   3. Populated-artifact assertion — when the live /knowledge/wiki
 *      response has a populated `results` array, the rendered
 *      `<button>` per entry shows the real title from the API. When
 *      clicking one entry, the expanded page-content area renders the
 *      real `content` body from GET /knowledge/wiki/{slug}.
 *
 * Bug-filing convention: every failing assertion here surfaces a real
 * bug filed as `.claude/backlog/items/2026-08-2X-knowledge-engine-<observed>.md`
 * per PROTOCOL.md.
 */

// Pin to IPv4. On macOS dev stacks the Vibe backend binds 127.0.0.1 only;
// Node's DNS resolver picks ::1 first for "localhost" and gets
// ECONNREFUSED intermittently (50/50 in our runs). Same pitfall as the
// Python urllib issue documented in tests/test_user_journeys.py, and as
// fixed for scheduled.spec.ts — see
// .claude/backlog/items/2026-08-30-vibetrading-e2e-ipv6-loopback-trap.md
const VIBE_API_ORIGIN = 'http://127.0.0.1:8899'

type WikiEntry = {
  score: number
  slug: string
  type: string
  title: string
  tags?: string[]
  related?: string[]
  sources?: string[]
  summary: string
}

type StrategyEntry = {
  key: string
  score: number
  label?: string
  market_view?: string
  risk_profile?: string
}

async function fetchLiveWiki(
  request: import('@playwright/test').APIRequestContext,
): Promise<{ ok: boolean; count: number; results: WikiEntry[] }> {
  const res = await request.get(`${VIBE_API_ORIGIN}/knowledge/wiki`, {
    params: { limit: '20' },
  })
  expect(res.status(), 'live /knowledge/wiki must respond 2xx').toBeLessThan(400)
  return (await res.json()) as { ok: boolean; count: number; results: WikiEntry[] }
}

async function fetchLiveWikiPage(
  request: import('@playwright/test').APIRequestContext,
  slug: string,
): Promise<{ ok: boolean; found: boolean; content?: string }> {
  const res = await request.get(
    `${VIBE_API_ORIGIN}/knowledge/wiki/${encodeURIComponent(slug)}`,
  )
  expect(res.status(), 'live /knowledge/wiki/{slug} must respond 2xx').toBeLessThan(400)
  return (await res.json()) as { ok: boolean; found: boolean; content?: string }
}

async function fetchLiveStrategies(
  request: import('@playwright/test').APIRequestContext,
  params: { marketView?: string; riskProfile?: string; limit?: string } = {},
): Promise<{ ok: boolean; count: number; results: StrategyEntry[] }> {
  const query: Record<string, string> = { limit: params.limit ?? '20' }
  if (params.marketView) query.market_view = params.marketView
  if (params.riskProfile) query.risk_profile = params.riskProfile
  const res = await request.get(`${VIBE_API_ORIGIN}/knowledge/strategies`, {
    params: query,
  })
  expect(res.status(), 'live /knowledge/strategies must respond 2xx').toBeLessThan(400)
  return (await res.json()) as { ok: boolean; count: number; results: StrategyEntry[] }
}

async function fetchLiveNewsDerived(
  request: import('@playwright/test').APIRequestContext,
  text?: string,
): Promise<{ ok: boolean; count: number; results: unknown[] }> {
  const res = await request.get(
    `${VIBE_API_ORIGIN}/knowledge/news-derived-strategies`,
    {
      params: { limit: '20', ...(text ? { text } : {}) },
    },
  )
  expect(res.status(), 'live /knowledge/news-derived-strategies must respond 2xx').toBeLessThan(400)
  return (await res.json()) as { ok: boolean; count: number; results: unknown[] }
}

test.describe('Knowledge Engine — render baseline (live API)', () => {
  test('mounts the Wiki tab with the real /knowledge/wiki response and no console errors', async ({
    page,
    request,
  }) => {
    const consoleErrors = trackConsoleErrors(page)
    const live = await fetchLiveWiki(request)
    console.log(
      `[knowledge-engine] live /knowledge/wiki count=${live.count} first_slug=${live.results[0]?.slug ?? 'none'}`,
    )

    await page.goto('/knowledge')

    // The page heading is "Knowledge Engine" with the description.
    await expect(
      page.getByText(/Browse the strategy catalog, wiki, and verified news-derived concepts/i),
    ).toBeVisible({ timeout: 15_000 })

    // All three tabs render.
    await expect(page.getByRole('button', { name: /^Wiki$/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /Strategy Catalog/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /News-Derived Concepts/ })).toBeVisible()

    // Wiki is the default tab — search bar is present and the
    // debounced (300ms) GET /knowledge/wiki fires from mount.
    await expect(
      page.getByPlaceholder(/Search the wiki \(concepts, terms, playbooks\)/i),
    ).toBeVisible()

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

  test('populated-artifact: wiki entries surface real titles from the live /knowledge/wiki response', async ({
    page,
    request,
  }) => {
    const live = await fetchLiveWiki(request)
    test.skip(
      live.results.length === 0,
      `live /knowledge/wiki returned ${live.count} entries with empty results; cannot assert titles`,
    )

    const firstTitle = live.results[0].title
    await page.goto('/knowledge')

    // The page renders the title of the first wiki entry — this catches
    // silent `?.` chain drops and shape-drift regressions in the render
    // path (e.g. `entry.title` becoming `entry.headline` upstream).
    await expect(page.getByText(firstTitle)).toBeVisible({ timeout: 15_000 })
  })
})

test.describe('Knowledge Engine — interaction (live API)', () => {
  test('clicking "Strategy Catalog" tab fires GET /knowledge/strategies and renders the populated cards', async ({
    page,
    request,
  }) => {
    const consoleErrors = trackConsoleErrors(page)
    const live = await fetchLiveStrategies(request, {})
    console.log(
      `[knowledge-engine] live /knowledge/strategies count=${live.count} first_label=${live.results[0]?.label ?? 'none'}`,
    )

    let strategiesRequestUrl: string | null = null
    page.on('request', (req) => {
      const u = req.url()
      if (
        req.method() === 'GET' &&
        u.includes('/knowledge/strategies') &&
        !u.includes('/track-record')
      ) {
        strategiesRequestUrl = u
      }
    })

    await page.goto('/knowledge')

    // Wait for the wiki tab's initial GET /knowledge/wiki to finish,
    // so the request-listener below only catches the strategies one.
    await expect(
      page.getByPlaceholder(/Search the wiki \(concepts, terms, playbooks\)/i),
    ).toBeVisible({ timeout: 15_000 })
    await page.waitForResponse(
      (r) => r.url().includes('/knowledge/wiki') && !r.url().includes('/strategies'),
      { timeout: 15_000 },
    )

    // Click the Strategy Catalog tab.
    await page.getByRole('button', { name: /Strategy Catalog/ }).click()

    await expect.poll(() => strategiesRequestUrl, {
      timeout: 15_000,
      message: 'expected GET /knowledge/strategies to fire after tab click',
    }).not.toBeNull()

    // Verify the URL contains the right host + path (no SPA fallback
    // intercepted the request and the live backend answered).
    expect(strategiesRequestUrl).toMatch(/^http:\/\/127\.0\.0\.1:8899\/knowledge\/strategies/)

    // The market-view filter input is rendered on this tab.
    await expect(
      page.getByPlaceholder(/Market view \(e.g. bullish\)/i),
    ).toBeVisible()
    await expect(
      page.getByPlaceholder(/Risk profile \(e.g. conservative\)/i),
    ).toBeVisible()

    // At least one of the live strategy labels is rendered. This
    // catches the silent-fallback class where the response is OK
    // but the card never mounts. Use `.first()` because indicator-tag
    // chips (e.g. "constituent_momentum_7d") substring-match against
    // the strategy label "Momentum" — the card title is the first hit.
    const firstLabel = live.results[0]?.label ?? live.results[0]?.key
    test.skip(!firstLabel, 'no live strategy label/key to assert on')
    await expect(page.getByText(firstLabel!, { exact: true }).first()).toBeVisible({
      timeout: 15_000,
    })

    expect(consoleErrors, `unexpected console errors:\n${consoleErrors.join('\n')}`).toEqual([])
  })

  test('typing in the Market view filter narrows the request to ?market_view=bullish and stays 2xx', async ({
    page,
    request,
  }) => {
    const live = await fetchLiveStrategies(request, {
      marketView: 'bullish',
      riskProfile: 'defined_risk',
    })
    console.log(
      `[knowledge-engine] live /knowledge/strategies?market_view=bullish&risk_profile=defined_risk count=${live.count}`,
    )

    let lastStrategiesUrl: string | null = null
    page.on('request', (req) => {
      const u = req.url()
      if (req.method() === 'GET' && u.includes('/knowledge/strategies')) {
        lastStrategiesUrl = u
      }
    })

    await page.goto('/knowledge')
    await page.getByRole('button', { name: /Strategy Catalog/ }).click()
    await expect(
      page.getByPlaceholder(/Market view \(e.g. bullish\)/i),
    ).toBeVisible({ timeout: 15_000 })

    // Wait for the initial /knowledge/strategies (no params) to land.
    await page.waitForResponse(
      (r) => r.url().includes('/knowledge/strategies'),
      { timeout: 15_000 },
    )

    // Type into the Market view filter — the debounced reload fires
    // GET /knowledge/strategies?market_view=bullish&...
    await page.getByPlaceholder(/Market view \(e.g. bullish\)/i).fill('bullish')

    await expect
      .poll(() => lastStrategiesUrl, {
        timeout: 15_000,
        message: 'expected debounced GET /knowledge/strategies?market_view=bullish',
      })
      .toMatch(/market_view=bullish/)
  })

  test('clicking "News-Derived Concepts" tab fires GET /knowledge/news-derived-strategies and renders the empty-state', async ({
    page,
    request,
  }) => {
    const live = await fetchLiveNewsDerived(request)
    console.log(
      `[knowledge-engine] live /knowledge/news-derived-strategies count=${live.count} (live DB has 0 verified concepts at audit time)`,
    )

    let newsDerivedRequestUrl: string | null = null
    page.on('request', (req) => {
      const u = req.url()
      if (req.method() === 'GET' && u.includes('/knowledge/news-derived-strategies')) {
        newsDerivedRequestUrl = u
      }
    })

    await page.goto('/knowledge')
    await page.getByRole('button', { name: /News-Derived Concepts/ }).click()

    await expect.poll(() => newsDerivedRequestUrl, {
      timeout: 15_000,
      message: 'expected GET /knowledge/news-derived-strategies to fire after tab click',
    }).not.toBeNull()

    // The empty-state copy is the only thing the user can see when
    // the live count is 0. Asserting on it catches the regression
    // class where a populated payload was assumed and the "no
    // concepts yet" copy was deleted.
    await expect(
      page.getByText(
        /No news-derived concepts yet.* extracted and verified from live news as they arrive/i,
      ),
    ).toBeVisible({ timeout: 15_000 })
  })
})

test.describe('Knowledge Engine — wiki page expand (live API)', () => {
  test('clicking a wiki entry expands the page content from GET /knowledge/wiki/{slug}', async ({
    page,
    request,
  }) => {
    const consoleErrors = trackConsoleErrors(page)
    const wikiList = await fetchLiveWiki(request)
    test.skip(
      wikiList.results.length === 0,
      'live /knowledge/wiki returned no entries; cannot exercise page-content expand',
    )

    const firstSlug = wikiList.results[0].slug
    const wikiPage = await fetchLiveWikiPage(request, firstSlug)
    console.log(
      `[knowledge-engine] live /knowledge/wiki/${firstSlug} found=${wikiPage.found} content_len=${wikiPage.content?.length ?? 0}`,
    )

    let pageRequestUrl: string | null = null
    page.on('request', (req) => {
      const u = req.url()
      if (req.method() === 'GET' && u.includes('/knowledge/wiki/')) {
        pageRequestUrl = u
      }
    })

    await page.goto('/knowledge')
    await expect(
      page.getByPlaceholder(/Search the wiki \(concepts, terms, playbooks\)/i),
    ).toBeVisible({ timeout: 15_000 })
    await page.waitForResponse(
      (r) => r.url().includes('/knowledge/wiki') && !u_includesStrategies(r.url()),
      { timeout: 15_000 },
    )

    // Click the first wiki entry to expand it.
    await page.getByText(wikiList.results[0].title).click()

    await expect.poll(() => pageRequestUrl, {
      timeout: 15_000,
      message: `expected GET /knowledge/wiki/${firstSlug} to fire after entry click`,
    }).not.toBeNull()
    expect(pageRequestUrl).toContain(`/knowledge/wiki/${encodeURIComponent(firstSlug)}`)

    // The page-content body is rendered inside a <pre> block.
    // Match the first 60 chars of the content so we don't depend on
    // exact wording (the body is markdown prose).
    if (wikiPage.found && wikiPage.content) {
      const snippet = wikiPage.content.slice(0, 60).trim()
      await expect(page.locator('pre').filter({ hasText: snippet }).first()).toBeVisible({
        timeout: 15_000,
      })
    }

    expect(consoleErrors, `unexpected console errors:\n${consoleErrors.join('\n')}`).toEqual([])
  })
})

// helper kept local to avoid coupling with helpers/apiMock.ts
function u_includesStrategies(u: string): boolean {
  return u.includes('/knowledge/strategies')
}
