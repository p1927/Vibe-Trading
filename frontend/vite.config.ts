import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { nodePolyfills } from "vite-plugin-node-polyfills";
import path from "path";

const PROXY_PATHS = [
  "/auth",
  "/sessions",
  "/swarm/presets",
  "/swarm/runs",
  "/qveris",
  "/settings/llm",
  "/settings/data-sources",
  "/channels",
  "/mandate",
  "/autonomous-agents",
  "/live",
  "/upload",
  "/shadow-reports",
  "/trade",
  "/trading",
  "/scheduled-runs",
  "/options",
];

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.VITE_API_URL || "http://127.0.0.1:8899";
  const apiProxy = {
    target: apiTarget,
    changeOrigin: true,
    configure: (proxy: {
      on: (event: string, handler: (...args: unknown[]) => void) => void;
    }) => {
      proxy.on("proxyReq", (proxyReq: { setHeader: (name: string, value: string) => void }, req) => {
        const url = req.url ?? "";
        if (url.includes("/stream") || req.headers?.accept?.includes("text/event-stream")) {
          proxyReq.setHeader("Accept", "text/event-stream");
          proxyReq.setHeader("Cache-Control", "no-cache");
        }
      });
      proxy.on("proxyRes", (proxyRes: { headers: Record<string, string | string[] | undefined> }) => {
        const ct = proxyRes.headers["content-type"];
        const contentType = Array.isArray(ct) ? ct[0] : ct;
        if (contentType?.includes("text/event-stream")) {
          proxyRes.headers["cache-control"] = "no-cache";
          proxyRes.headers["x-accel-buffering"] = "no";
        }
      });
    },
  };
  const apiProxyWithHtmlFallback = {
    ...apiProxy,
    bypass(req: { headers: { accept?: string } }) {
      if (req.headers.accept?.includes("text/html")) {
        return "/index.html";
      }
    },
  };

  const proxy = {
    ...Object.fromEntries(PROXY_PATHS.map((p) => [p, apiProxy])),
    // SPA RunDetail page — only the two-segment ``/runs/{id}``
    // form should fall back to ``index.html`` on browser navigation.
    // ``/runs/{id}/code`` and ``/runs/{id}/pine`` are API-only and
    // must keep proxying to the backend even when Accept is text/html.
    "^/runs/[^/]+/?$": apiProxyWithHtmlFallback,
    "/runs": apiProxy,
    "/correlation": apiProxyWithHtmlFallback,
    "/prediction": apiProxyWithHtmlFallback,
    // /options is both the SPA Options Lab route and an API prefix
    // (/options/payoff, /options/chain) — same dual role as /correlation.
    // Overrides the plain PROXY_PATHS entry above.
    "/options": apiProxyWithHtmlFallback,
    "^/alpha(?:/|$)": apiProxy,
  };

  return {
    plugins: [
      react(),
      nodePolyfills({
        include: ["buffer", "stream", "util"],
        globals: {
          Buffer: true,
          global: true,
          process: true,
        },
        protocolImports: true,
      }),
    ],
    define: {
      global: "globalThis",
    },
    resolve: {
      alias: { "@": path.resolve(import.meta.dirname, "./src") },
    },
    optimizeDeps: {
      include: [
        "plotly.js/lib/core",
        "plotly.js/lib/scatter",
        "plotly.js/lib/bar",
        "plotly.js/lib/candlestick",
        "react-plotly.js/factory",
      ],
    },
    server: {
      host: true,
      port: 5899,
      strictPort: true,
      proxy,
    },
    preview: {
      host: true,
      port: 5899,
      strictPort: true,
      proxy,
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: (id: string) => {
            if (/node_modules\/(react|react-dom|react-router)\//.test(id)) return "vendor-react";
            if (/node_modules\/(echarts|plotly\.js|react-plotly\.js|lightweight-charts)\//.test(id)) return "vendor-charts";
            return undefined;
          },
        },
      },
    },
  };
});
