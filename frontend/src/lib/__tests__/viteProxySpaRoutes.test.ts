import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import viteConfig from "../../../vite.config";

type ProxyEntry = { bypass?: (req: { headers: { accept?: string } }) => string | undefined };

const ROOT = resolve(__dirname, "../../..");

async function proxyTable(): Promise<Record<string, ProxyEntry>> {
  const cfg = typeof viteConfig === "function" ? await viteConfig({ mode: "test", command: "serve" }) : await viteConfig;
  return (cfg as { server?: { proxy?: Record<string, ProxyEntry> } }).server?.proxy ?? {};
}

// A browser path that is both an SPA page and an API prefix must fall back to index.html on a page
// navigation (Accept: text/html), or a hard reload in `vite dev` is proxied to the API.
describe("vite dev proxy: SPA routes that share a prefix with an API proxy entry", () => {
  it("every such route bypasses to index.html for a page navigation", async () => {
    const proxy = await proxyTable();
    const router = readFileSync(resolve(ROOT, "src/router.tsx"), "utf8");
    const routes = [...router.matchAll(/path:\s*"(\/[^":]*)"/g)].map((m) => m[1]).filter((p) => p !== "/");
    expect(routes).toContain("/knowledge");
    const offenders: string[] = [];
    for (const route of routes) {
      for (const [key, entry] of Object.entries(proxy)) {
        const hit = key.startsWith("^") ? new RegExp(key).test(route) : route === key || route.startsWith(`${key}/`);
        if (!hit) continue;
        if (entry.bypass?.({ headers: { accept: "text/html,application/xhtml+xml" } }) !== "/index.html") {
          offenders.push(`${route} -> ${key}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("an API call (Accept: application/json) is still proxied", async () => {
    const proxy = await proxyTable();
    expect(proxy["/knowledge"].bypass?.({ headers: { accept: "application/json" } })).toBeUndefined();
    expect(proxy["/execution-advisor"].bypass?.({ headers: { accept: "application/json" } })).toBeUndefined();
  });
});
