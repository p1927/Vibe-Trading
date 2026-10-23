import { expect, test as base, type APIRequestContext } from '@playwright/test'

/**
 * The ONE Playwright `test` for specs that talk to the real stack (Vibe API :8899,
 * stock simulator :8902, OpenAlgo :5001). A live-stack run must never change state
 * on an account that may hold real money, so every write (anything but GET/HEAD/
 * OPTIONS) to those services is refused unless the operator states the stack is a
 * paper one: E2E_ALLOW_LIVE_WRITES=1 AND E2E_STACK_PAPER=1. The release tier's ports
 * are never writable. Applies to browser traffic (context route) and to the
 * `request` fixture alike.
 */
const DEV_PORTS = ['8899', '8902', '5001']
const RELEASE_PORTS = ['8909', '8912', '5011']
const READ_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

export const writesAllowed = process.env.E2E_ALLOW_LIVE_WRITES === '1' && process.env.E2E_STACK_PAPER === '1'

/** Why a request must not be sent, or null when it may go. */
export function blockedReason(method: string, url: string): string | null {
  if (READ_METHODS.has(method.toUpperCase())) return null
  let port: string
  try {
    port = new URL(url).port
  } catch {
    return null
  }
  // Chat sessions only create conversation records (the order path has its own mandate gate), so POST /sessions* stays open.
  const path = (() => { try { return new URL(url).pathname } catch { return '' } })()
  if (DEV_PORTS.includes(port) && /^\/sessions(\/|$)/.test(path)) return null
  if (RELEASE_PORTS.includes(port)) return `write to release-tier port ${port} is never allowed`
  if (DEV_PORTS.includes(port) && !writesAllowed) {
    return `live-stack write ${method} :${port} refused (set E2E_ALLOW_LIVE_WRITES=1 and E2E_STACK_PAPER=1 only against a paper stack)`
  }
  return null
}

const WRITE_FNS = new Set(['post', 'put', 'patch', 'delete', 'fetch'])

function guardRequest(request: APIRequestContext): APIRequestContext {
  return new Proxy(request, {
    get(target, prop) {
      const v = Reflect.get(target, prop, target)
      if (typeof prop !== 'string' || !WRITE_FNS.has(prop) || typeof v !== 'function') {
        return typeof v === 'function' ? v.bind(target) : v
      }
      return (url: string, options?: { method?: string }) => {
        const reason = blockedReason(prop === 'fetch' ? (options?.method ?? 'GET') : prop, url)
        if (reason) throw new Error(reason)
        return v.call(target, url, options)
      }
    },
  })
}

export const test = base.extend({
  context: async ({ context }, use) => {
    await context.route(/:(8899|8902|5001|8909|8912|5011)\//, async (route) => {
      const req = route.request()
      const reason = blockedReason(req.method(), req.url())
      if (reason) {
        await route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: reason }) })
      } else {
        await route.fallback()
      }
    })
    await use(context)
  },
  request: async ({ request }, use) => {
    await use(guardRequest(request))
  },
})

export { expect }
