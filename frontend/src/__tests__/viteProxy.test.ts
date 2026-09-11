// @vitest-environment node
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import viteConfig from "../../vite.config";

/**
 * Every API path the frontend calls must be proxied by the Vite dev/preview server.
 *
 * A prefix missing from `vite.config.ts`'s proxy does not fail loudly: Vite's SPA history
 * fallback answers it with `index.html`, HTTP 200, `text/html`, and the caller dies parsing
 * JSON. `/board` (the whole Agent/Advisory board surface) shipped that way; `resolveApiBase()`
 * only masks it on the known UI ports. So rather than hand-listing a few prefixes, this test
 * derives the paths from the API client source and checks each one against the proxy object the
 * real config builds — adding a new prefix to `src/lib/api.ts` without a proxy entry fails here,
 * naming the prefix. (Trade backlog: 2026-09-07-vite-proxy-swallows-unlisted-prefixes.)
 */

const SRC = path.resolve(__dirname, "..");
const CLIENT_FILES = ["lib/api.ts", "lib/apiAuth.ts"];

/** Static head of a literal up to its first `${...}`, placeholder kept as "x" after a "/". */
function normalise(literal: string): string {
  const cut = literal.indexOf("${");
  let head = cut === -1 ? literal : literal.slice(0, cut);
  if (cut !== -1 && head.endsWith("/")) head += "x";
  return head.split("?")[0];
}

/** String/template literal starting at text[i] (a quote char); returns its raw body. */
function readLiteral(text: string, i: number): string | null {
  const q = text[i];
  if (q !== "`" && q !== '"' && q !== "'") return null;
  let j = i + 1;
  let body = "";
  while (j < text.length && text[j] !== q) {
    body += text[j];
    j += 1;
  }
  return body;
}

/** First argument of every `request(...)` / `request<T>(...)` call, when it is a literal. */
function requestPaths(text: string): string[] {
  const out: string[] = [];
  for (const m of text.matchAll(/\brequest\b/g)) {
    let i = (m.index ?? 0) + m[0].length;
    if (text[i] === "<") {
      // Skip a balanced generic argument, e.g. request<{ a: Record<string, boolean> }>(
      let depth = 0;
      for (; i < text.length; i += 1) {
        if (text[i] === "<") depth += 1;
        else if (text[i] === ">" && text[i - 1] !== "=") {
          depth -= 1;
          if (depth === 0) {
            i += 1;
            break;
          }
        }
      }
    }
    if (text[i] !== "(") continue;
    i += 1;
    while (/\s/.test(text[i])) i += 1;
    const lit = readLiteral(text, i);
    if (lit !== null && lit.startsWith("/")) out.push(normalise(lit));
  }
  return out;
}

/** `${BASE}/...` URLs and relative `fetch("/...")` calls. */
function directPaths(text: string): string[] {
  const viaBase = [...text.matchAll(/\$\{BASE\}(\/[^`"'\s]*)/g)].map((m) => normalise(m[1]));
  const relative = [...text.matchAll(/\bfetch\(\s*(["'`])(\/[^"'`]*)\1/g)].map((m) => normalise(m[2]));
  return [...viaBase, ...relative];
}

function clientApiPaths(): string[] {
  const all = CLIENT_FILES.flatMap((f) => {
    const text = fs.readFileSync(path.join(SRC, f), "utf8");
    return [...requestPaths(text), ...directPaths(text)];
  });
  return [...new Set(all)].sort();
}

function proxyKeys(): string[] {
  const resolved =
    typeof viteConfig === "function"
      ? viteConfig({ mode: "test", command: "serve", isSsrBuild: false, isPreview: false })
      : viteConfig;
  const proxy = (resolved as { server?: { proxy?: Record<string, unknown> } }).server?.proxy;
  return Object.keys(proxy ?? {});
}

/** Vite's matching rule: a key starting with "^" is a RegExp, otherwise a plain prefix. */
function isProxied(p: string, keys: string[]): boolean {
  return keys.some((k) => (k.startsWith("^") ? new RegExp(k).test(p) : p.startsWith(k)));
}

describe("Vite API proxy config", () => {
  const keys = proxyKeys();
  const paths = clientApiPaths();

  it("extracts the client's API paths (guards against the extractor silently finding nothing)", () => {
    // api.ts carries ~280 request() calls; a refactor that breaks extraction must fail loudly
    // rather than let the coverage assertion below pass vacuously.
    expect(paths.length).toBeGreaterThan(100);
    expect(paths).toContain("/auth/sse-ticket");
  });

  it("proxies every API path the client calls", () => {
    const unproxied = paths.filter((p) => !isProxied(p, keys));
    const prefixes = [...new Set(unproxied.map((p) => "/" + p.split("/")[1]))];
    expect(
      prefixes,
      `API prefixes called from ${CLIENT_FILES.join(", ")} but missing from vite.config.ts's ` +
        `proxy (they would get the SPA's index.html with HTTP 200): ${unproxied.join(", ")}`,
    ).toEqual([]);
  });

  it("matcher reports an uncovered path as unproxied and a covered one as proxied", () => {
    // Sanity check on the matcher itself, so the coverage assertion above cannot pass vacuously.
    expect(isProxied("/definitely-not-an-api-prefix/x", keys)).toBe(false);
    expect(isProxied("/board/agent/x/summary", keys)).toBe(true);
  });
});
