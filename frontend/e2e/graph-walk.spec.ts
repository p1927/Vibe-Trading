import { expect, test, type Page } from '@playwright/test'
import { hasErrorBoundary } from './helpers/apiMock'

/**
 * Scripted state-graph walker for exploratory bug-finding (backlog item
 * 2026-08-21-vibetrading-frontend-playwright-e2e-harness's "click around and
 * find bugs" goal). A hand-declared node/edge graph over the routes in
 * src/router.tsx, BFS-walked, asserting no console error and no React error
 * boundary fires at each node.
 *
 * This is intentionally a small custom runner (~150 lines), not a
 * heavyweight state-graph tool like GraphWalker/AltWalker — per the backlog
 * item's own explicit instruction that those are a poor fit here (Java/YAML
 * centric, nothing else in this repo uses them).
 *
 * Edges here are declared but walked as direct `page.goto(node.path)` calls
 * rather than simulated nav-link clicks: src/components/layout/Layout.tsx
 * only surfaces a handful of routes as clickable nav links (home, agent),
 * not all ~15 router.tsx routes, so there is no reliable in-app click path
 * to most nodes. The task's own instructions call this an acceptable first
 * version, to be noted in the report. The graph/edge/BFS shape is kept
 * exactly as specified — only the edge *traversal mechanism* is a goto
 * instead of a click — so a future pass can swap in real nav-link clicks for
 * whichever edges do have one, without changing the graph structure.
 *
 * Rewritten 2026-08-28 to talk to the real Vibe Trading API on :8899 — NO
 * `installBaselineApiMock`, NO mocks of any kind, per the standing rule that
 * E2E tests must run against the live stack (see testing/README.md's
 * "Running the tests" section and docs/audits/2026-08-27-test-coverage-handoff.md).
 */

interface GraphNode {
  id: string
  path: string
}

interface GraphEdge {
  from: string
  to: string
}

// Static routes only (src/router.tsx) — dynamic routes like /runs/:runId and
// /alpha-zoo/:alphaId are excluded, since they need a real record id and
// aren't part of the "click around the app" surface this walker targets.
const NODES: GraphNode[] = [
  { id: 'Home', path: '/' },
  { id: 'Agent', path: '/agent' },
  { id: 'Autonomous', path: '/autonomous' },
  { id: 'Runtime', path: '/runtime' },
  { id: 'Scheduled', path: '/scheduled' },
  { id: 'Reports', path: '/reports' },
  { id: 'Settings', path: '/settings' },
  { id: 'ModelAdapters', path: '/model-adapters' },
  { id: 'Compare', path: '/compare' },
  { id: 'Correlation', path: '/correlation' },
  { id: 'Prediction', path: '/prediction' },
  { id: 'Hub', path: '/hub' },
  { id: 'Simulator', path: '/simulator' },
  { id: 'OptionsLab', path: '/options' },
  { id: 'AlphaZoo', path: '/alpha-zoo' },
]

// A star graph from Home to every other node is enough to cover every route
// at least once via BFS, while staying a genuine node/edge graph (not just a
// flat list) that a future pass can extend with real nav-link edges.
const EDGES: GraphEdge[] = NODES.filter((n) => n.id !== 'Home').map((n) => ({
  from: 'Home',
  to: n.id,
}))

function neighbors(nodeId: string): string[] {
  return EDGES.filter((e) => e.from === nodeId).map((e) => e.to)
}

function nodeById(id: string): GraphNode {
  const node = NODES.find((n) => n.id === id)
  if (!node) throw new Error(`Unknown graph node: ${id}`)
  return node
}

interface VisitResult {
  nodeId: string
  path: string
  consoleErrors: string[]
  errorBoundary: boolean
}

async function visitNode(page: Page, node: GraphNode): Promise<VisitResult> {
  const consoleErrors: string[] = []
  const onConsole = (msg: import('@playwright/test').ConsoleMessage) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text())
  }
  const onPageError = (err: Error) => consoleErrors.push(err.message)
  page.on('console', onConsole)
  page.on('pageerror', onPageError)

  try {
    await page.goto(node.path)
    // Let lazy-loaded route chunks and their first-effect fetches settle.
    // `waitForLoadState` has no timeout of its own — it silently inherits
    // whatever test-timeout budget remains, so a page with continuous
    // background polling/SSE (several routes here do, e.g. Simulator's
    // multi-symbol spot polling) never reaches "networkidle" and burns
    // nearly the *entire remaining test timeout* on this one call before
    // falling through to the catch (confirmed live: this starved later
    // nodes and blew the whole BFS walk's budget once mocks were removed
    // and real per-request latency replaced near-instant mocked responses).
    // Cap it explicitly so a polling page fails fast into the fixed
    // settle window instead.
    await page.waitForLoadState('networkidle', { timeout: 4_000 }).catch(() => {
      // A page that keeps a live poll/SSE connection open never reaches
      // "networkidle" — fall back to a fixed settle window instead.
    })
    await page.waitForTimeout(500)
    const errorBoundary = await hasErrorBoundary(page)
    return { nodeId: node.id, path: node.path, consoleErrors: [...consoleErrors], errorBoundary }
  } finally {
    page.off('console', onConsole)
    page.off('pageerror', onPageError)
  }
}

/** Plain BFS over the declared graph, starting from Home, visiting each
 *  reachable node exactly once and recording a result per node. */
async function bfsWalk(page: Page, startId: string): Promise<VisitResult[]> {
  const visited = new Set<string>()
  const queue: string[] = [startId]
  const results: VisitResult[] = []

  while (queue.length > 0) {
    const currentId = queue.shift()!
    if (visited.has(currentId)) continue
    visited.add(currentId)

    results.push(await visitNode(page, nodeById(currentId)))

    for (const next of neighbors(currentId)) {
      if (!visited.has(next)) queue.push(next)
    }
  }

  return results
}

test.describe('State-graph walk', () => {
  test('BFS-walks every declared route with no console errors or error boundary', async ({ page }) => {
    // 15 routes * ~2s settle each genuinely needs more than Playwright's 30s
    // default, especially under parallel-worker CPU contention (observed:
    // 30.4s under 5 concurrent workers, 32s alone) — bump rather than let
    // this flake on resource pressure.
    test.setTimeout(90_000)

    const results = await bfsWalk(page, 'Home')

    // Every declared node was actually visited.
    expect(results.map((r) => r.nodeId).sort()).toEqual(NODES.map((n) => n.id).sort())

    const failures = results.filter((r) => r.errorBoundary || r.consoleErrors.length > 0)
    if (failures.length > 0) {
      const report = failures
        .map((f) => `  ${f.nodeId} (${f.path}): errorBoundary=${f.errorBoundary}, console=${JSON.stringify(f.consoleErrors)}`)
        .join('\n')
      throw new Error(`Graph walk found ${failures.length} node(s) with issues:\n${report}`)
    }
  })
})
