import { expect, test } from '@playwright/test'

/**
 * Stock Simulator standalone-backend E2E — exercises the live
 * `integrations/trade_integrations/stock_simulator/service` FastAPI
 * directly on `:8902`, the single source of truth for the replay
 * clock, replay data, and live quotes.
 *
 * Why this exists alongside `simulator.spec.ts`: the existing
 * `simulator.spec.ts` lives behind the Vibe Trading API on
 * `:8899` (the Vibe proxy that forwards recording/replay and
 * market-data/ticks calls to the live stack). That half of the
 * surface requires `:8899 + :5001 + :5899` all up — a state that
 * does not hold in every session (see
 * `.claude/backlog/items/2026-08-29-dev-stack-half-down-e2e-blocker`).
 *
 * This spec deliberately routes around the Vibe proxy and hits the
 * standalone stock-simulator service directly. It's a parallel E2E
 * coverage of the same screen (`/simulator`) against a different
 * dependency in the same chain — exactly the "what still works if the
 * proxy is dead" view that the handoff's "file when the stack is
 * down, don't paper it over with mocks" rule asks for.
 *
 * Three tiers, mirroring `simulator.spec.ts`'s structure:
 *
 *   1. Render baseline: `GET /health` is reachable without auth and
 *      reports a real running_commit + started_at. /health is the
 *      only auth-free surface, so it doubles as the "stack is up"
 *      preflight that the handoff recommends surfaces as a finding
 *      when missing.
 *
 *   2. Auth-gating check: every control/data endpoint used below
 *      returns 401 without the `X-Simulator-Control-Token` header —
 *      a regression that opens them up would silently allow
 *      unauthorized replay control.
 *
 *   3. Populated-artifact tier (when `STOCK_SIMULATOR_CONTROL_TOKEN`
 *      is set in the Playwright process env, e.g. by the CI runner
 *      sourcing `.env` first, or by a developer running
 *      `set -a && source .env && set +a` then `npx playwright test`):
 *      `data/quote?symbol=NIFTY&exchange=NSE_INDEX`,
 *      `control/replay/status`, `control/replay/calendar`, and a
 *      full `/control/replay/speed` mutation round-trip all
 *      return live, expected-shape JSON.
 *
 * Bug-filing convention: every failing assertion here surfaces a
 * real bug filed as `.claude/backlog/items/2026-08-2X-...` per
 * PROTOCOL.md.
 */

const STOCK_SIM_API_ORIGIN = 'http://localhost:8902'
const TOKEN_HEADER = 'X-Simulator-Control-Token'
const TOKEN_ENV_VAR = 'STOCK_SIMULATOR_CONTROL_TOKEN'

function readControlToken(): string | null {
  // Set by `set -a && source .env && set +a && npx playwright test`,
  // by CI's secret-injection step, or by Playwright's
  // `globalSetup`. We deliberately don't read `.env` from the test
  // process — vibetrading/frontend's tsconfig doesn't include
  // `@types/node` and reading Node builtins through Playwright's
  // on-the-fly TS compile is brittle across engines.
  const t = process.env[TOKEN_ENV_VAR]
  return t && t.trim().length > 0 ? t.trim() : null
}

test.describe('Stock Simulator — standalone service :8902 (live backend)', () => {
  test('/health is reachable without auth and reports a running commit', async ({ request }) => {
    const res = await request.get(`${STOCK_SIM_API_ORIGIN}/health`)
    expect(res.status()).toBe(200)
    const body = (await res.json()) as {
      status?: string
      running_commit?: string
      started_at?: string
    }
    expect(body.status).toBe('ok')
    // running_commit / started_at are documented in the service's
    // own /health response — asserting they're present and shaped
    // correctly catches a silent revert to a stubbed health check.
    expect(typeof body.running_commit).toBe('string')
    expect(body.running_commit).toMatch(/^[0-9a-f]{7,40}$/)
    expect(typeof body.started_at).toBe('string')
    expect(Number.isNaN(Date.parse(body.started_at as string))).toBe(false)
  })

  test('/control/* endpoints reject unauthenticated callers with 401 (auth gate)', async ({
    request,
  }) => {
    // The same-shape assertion across multiple endpoints — a
    // regression that opened any of them up (or that broke the
    // gating middleware entirely) would surface here. No token
    // supplied → expect 401.
    for (const ep of [
      '/control/replay/status',
      '/control/replay/calendar',
      '/data/quote?symbol=NIFTY&exchange=NSE_INDEX',
      '/history/coverage',
    ]) {
      const res = await request.get(`${STOCK_SIM_API_ORIGIN}${ep}`)
      expect(
        res.status(),
        `unauthenticated ${ep} should be 401 but got ${res.status()} — auth gate is broken or this endpoint is unauthed`,
      ).toBe(401)
    }
  })

  test('/data/quote returns a live stock_simulator quote for NIFTY when armed with the control token', async ({
    request,
  }) => {
    const token = readControlToken()
    if (!token) {
      test.skip(
        true,
        `STOCK_SIMULATOR_CONTROL_TOKEN not set in env — cannot exercise token-gated control/data endpoints (run with \`set -a && source ../../.env && set +a\` first)`,
      )
      return
    }
    const res = await request.get(
      `${STOCK_SIM_API_ORIGIN}/data/quote?symbol=NIFTY&exchange=NSE_INDEX`,
      { headers: { [TOKEN_HEADER]: token } },
    )
    expect(res.status()).toBe(200)
    const body = (await res.json()) as {
      status?: string
      mode?: string
      data?: {
        symbol?: string
        exchange?: string
        ltp?: number
        source?: string
        simulated?: boolean
        sim_ts?: string
      }
    }
    expect(body.status).toBe('ok')
    expect(['replay', 'live']).toContain(body.mode ?? '')
    const q = body.data ?? {}
    expect(q.symbol).toBe('NIFTY')
    expect(q.exchange).toBe('NSE_INDEX')
    expect(typeof q.ltp).toBe('number')
    expect((q.ltp as number) > 0).toBe(true)
    expect(q.source).toBe('stock_simulator')
    expect(typeof q.simulated).toBe('boolean')
    expect(typeof q.sim_ts).toBe('string')
    expect(Number.isNaN(Date.parse(q.sim_ts as string))).toBe(false)
  })

  test('/control/replay/calendar reports the recorded days present in the live data root', async ({
    request,
  }) => {
    const token = readControlToken()
    if (!token) {
      test.skip(true, 'STOCK_SIMULATOR_CONTROL_TOKEN not set in env')
      return
    }
    const res = await request.get(`${STOCK_SIM_API_ORIGIN}/control/replay/calendar`, {
      headers: { [TOKEN_HEADER]: token },
    })
    expect(res.status()).toBe(200)
    const body = (await res.json()) as {
      status?: string
      days?: Array<{ date: string }>
    }
    expect(body.status).toBe('ok')
    // Live shape (`stock_simulator/service/control.py::replay_calendar`
    // observable output): { status: "ok", days: [...] }. There is no
    // `configured` flag here — empty `days` is the "unconfigured"
    // signal (a regression that returned `configured: false` with
    // non-empty days, or a populated `days` with `status != "ok"`,
    // would surface here).
    expect(Array.isArray(body.days)).toBe(true)
    for (const d of body.days ?? []) {
      expect(typeof d.date).toBe('string')
      expect(d.date).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    }
  })

  test('/control/replay/status reflects live SimClock state with a parseable IST sim_now', async ({
    request,
  }) => {
    const token = readControlToken()
    if (!token) {
      test.skip(true, 'STOCK_SIMULATOR_CONTROL_TOKEN not set in env')
      return
    }
    const res = await request.get(`${STOCK_SIM_API_ORIGIN}/control/replay/status`, {
      headers: { [TOKEN_HEADER]: token },
    })
    expect(res.status()).toBe(200)
    const body = (await res.json()) as {
      status?: string
      mode?: string
      clock?: {
        replay_date?: string
        sim_now?: string
        speed?: number
        loop?: boolean
        stepped?: boolean
        paused?: boolean
        session_open?: boolean
        week_mode?: boolean
        week_dates?: string[]
      }
    }
    expect(body.status).toBe('ok')
    expect(['replay', 'live']).toContain(body.mode ?? '')
    const c = body.clock ?? {}
    expect(typeof c.replay_date).toBe('string')
    expect(c.replay_date).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(typeof c.sim_now).toBe('string')
    // sim_now must be a parseable ISO timestamp.
    expect(Number.isNaN(Date.parse(c.sim_now as string))).toBe(false)
    expect(typeof c.speed).toBe('number')
    expect(c.speed).toBeGreaterThanOrEqual(0)
    expect(typeof c.loop).toBe('boolean')
    expect(typeof c.stepped).toBe('boolean')
    expect(typeof c.paused).toBe('boolean')
    expect(typeof c.session_open).toBe('boolean')
    expect(typeof c.week_mode).toBe('boolean')
    if (c.week_mode) {
      expect(Array.isArray(c.week_dates)).toBe(true)
      // week_dates must contain the current replay_date (the
      // invariant the I3 DST-lite test guards; if the live
      // service ever returned a replay_date outside week_dates
      // this assertion would catch the same regression live).
      expect(c.week_dates!.includes(c.replay_date as string)).toBe(true)
    }
  })

  test('/control/replay/speed mutates /control/replay/status.clock.speed (real change, no mock)', async ({
    request,
  }) => {
    const token = readControlToken()
    if (!token) {
      test.skip(true, 'STOCK_SIMULATOR_CONTROL_TOKEN not set in env')
      return
    }
    // Snapshot the current speed.
    const beforeRes = await request.get(`${STOCK_SIM_API_ORIGIN}/control/replay/status`, {
      headers: { [TOKEN_HEADER]: token },
    })
    expect(beforeRes.status()).toBe(200)
    const beforeBody = (await beforeRes.json()) as {
      clock?: { speed?: number }
    }
    const beforeSpeed = beforeBody.clock?.speed
    expect(typeof beforeSpeed).toBe('number')

    // Pick a target that's measurably different. Avoid 0 (stepped)
    // and avoid values already at the cap (4.0). Use 2.0 if
    // current isn't already 2.0, else 3.0.
    const target = beforeSpeed === 2.0 ? 3.0 : 2.0

    // The live endpoint expects JSON body `{"speed": N}` — verified
    // by `curl -i -X POST -d '{"speed": 3.0}'` returning 200 (form
    // data returns 422 with Pydantic complaining about a non-object
    // body). Use the request fixture's json option so Playwright
    // sets the Content-Type header automatically.
    const postRes = await request.post(
      `${STOCK_SIM_API_ORIGIN}/control/replay/speed`,
      {
        headers: { [TOKEN_HEADER]: token },
        data: { speed: target },
      },
    )
    expect(postRes.status()).toBe(200)
    const postBody = (await postRes.json()) as {
      clock?: { speed?: number }
    }
    // The endpoint's response body also carries the new clock.speed —
    // assert the mutation landed in the same shape we read back from
    // /control/replay/status (catches a regression where the route
    // accepted the body but didn't mutate state).
    expect(postBody.clock?.speed).toBe(target)

    // Re-read /control/replay/status for the cross-check — a flaky
    // race between route completion and the next read is the kind
    // of thing that would also surface here.
    const afterRes = await request.get(`${STOCK_SIM_API_ORIGIN}/control/replay/status`, {
      headers: { [TOKEN_HEADER]: token },
    })
    expect(afterRes.status()).toBe(200)
    const afterBody = (await afterRes.json()) as {
      clock?: { speed?: number }
    }
    const afterSpeed = afterBody.clock?.speed
    expect(typeof afterSpeed).toBe('number')
    expect(afterSpeed).toBeGreaterThan(0)
    expect(afterSpeed).toBe(target)
  })
})
