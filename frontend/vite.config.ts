import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { nodePolyfills } from "vite-plugin-node-polyfills";
import path from "path";

const PROXY_PATHS = [
  "/sessions",
  "/swarm/presets",
  "/swarm/runs",
  "/qveris",
  "/settings/llm",
  "/settings/data-sources",
  "/channels",
  "/mandate",
  "/live",
  "/upload",
  "/shadow-reports",
];

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.VITE_API_URL || "http://127.0.0.1:8899";
  const apiProxy = { target: apiTarget, changeOrigin: true };
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
    "^/runs/[^/]+/?$": apiProxyWithHtmlFallback,
    "/runs": apiProxy,
    "/correlation": apiProxyWithHtmlFallback,
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
      alias: { "@": path.resolve(__dirname, "./src") },
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
          manualChunks: {
            "vendor-react": ["react", "react-dom", "react-router-dom"],
            "vendor-charts": ["echarts", "plotly.js", "react-plotly.js"],
          },
        },
      },
    },
  };
});
