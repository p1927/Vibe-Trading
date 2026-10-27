import fs from "node:fs";
import path from "node:path";
import { defineConfig, mergeConfig } from "vite";
import base from "./vite.config";

/**
 * Throwaway vite config for the mocked Playwright e2e (playwright.mock.config.ts): the app's own config
 * with no API proxy (so nothing can reach :8899/:8080) on an ephemeral port, and a cacheDir inside this
 * checkout so it never touches the shared node_modules/.vite.
 */
const port = Number(process.env.E2E_VITE_PORT ?? 49173);
const nodeModulesRoot = path.dirname(fs.realpathSync("node_modules")); // node_modules may be a symlink to another checkout

export default defineConfig((env) => {
  const cfg = mergeConfig(typeof base === "function" ? base(env) : base, {
    cacheDir: path.resolve(".e2e-cache/vite"),
    server: { port, fs: { allow: [process.cwd(), nodeModulesRoot] } },
  });
  cfg.server.proxy = {}; // mergeConfig keeps the base proxy keys otherwise
  return cfg;
});
