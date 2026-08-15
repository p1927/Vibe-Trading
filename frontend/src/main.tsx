import './i18n';
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";
import { Toaster } from "sonner";
import { ErrorBoundary } from "./components/common/ErrorBoundary";
import { router } from "./router";
// Self-hosted fonts (VT-006): vendor the woff2 files locally instead of the
// Google Fonts CDN. Weights match tailwind.config.ts (Inter 400/500/600/700,
// JetBrains Mono 400/500/700).
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/inter/700.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "@fontsource/jetbrains-mono/700.css";
import "highlight.js/styles/github-dark-dimmed.min.css";
import "./index.css";

// Forward uncaught window errors and unhandled promise rejections from the
// Vite SPA up to the Trade UI shell so the parent can show a per-iframe
// banner instead of leaving the iframe silently blank. The shell only
// accepts messages whose event.origin matches the iframe's data-origin,
// so we can post with "*" safely here. When the SPA is opened outside the
// shell (window.parent === window), the post is skipped.
function reportIframeError(message: string, stack?: string, url?: string) {
  if (typeof window === "undefined" || !window.parent || window.parent === window) {
    return;
  }
  try {
    window.parent.postMessage(
      {
        type: "iframe-error",
        source: "vibe",
        message,
        stack: stack || "",
        url: url || window.location?.href || "",
      },
      "*",
    );
  } catch {
    // Sandbox with allow-scripts but no allow-same-origin can throw here.
    // Nothing useful to do; the shell will still see the iframe blank.
  }
}

window.addEventListener("error", (event) => {
  // Vite's HMR client and dev tooling can throw their own internal errors
  // (e.g. websocket reconnect failures). Don't pollute the parent shell
  // with those — they're noise for users and not actionable. We still let
  // browser devtools show everything via console.error below.
  const filename = event.filename || "";
  const isViteInternal =
    filename.includes("/@vite/") ||
    filename.includes("/@id/") ||
    filename.includes("/node_modules/") ||
    filename === "";
  if (isViteInternal && !event.error) {
    return;
  }
  const message =
    event.error?.message ||
    event.message ||
    "Uncaught error in the Vite SPA";
  reportIframeError(
    message,
    event.error?.stack || "",
    filename ? `${filename}:${event.lineno}:${event.colno}` : "",
  );
  // Always log to devtools so the developer sees the full error there.
  if (event.error) {
    console.error("[vibe] uncaught error", event.error);
  }
});

window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason;
  const message =
    reason instanceof Error
      ? reason.message
      : typeof reason === "string"
        ? reason
        : "Unhandled promise rejection";
  const stack = reason instanceof Error ? reason.stack : "";
  reportIframeError(message, stack || "");
});

const prefetchMiniEquityChart = () => {
  void import("@/components/charts/MiniEquityChart");
};

const idleWindow = window as Window & {
  requestIdleCallback?: (
    callback: IdleRequestCallback,
    options?: IdleRequestOptions,
  ) => number;
};

if (typeof idleWindow.requestIdleCallback === "function") {
  idleWindow.requestIdleCallback(prefetchMiniEquityChart, { timeout: 2000 });
} else {
  window.setTimeout(prefetchMiniEquityChart, 0);
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <RouterProvider router={router} />
      <Toaster position="bottom-right" richColors closeButton duration={3500} />
    </ErrorBoundary>
  </StrictMode>
);
