import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

const isLightweightChartsNoise = (err: unknown): boolean => {
  const msg =
    err instanceof Error ? `${err.stack ?? err.message ?? ""}` : String(err);
  return (
    /lightweight-charts|ChartWidget|HorzScale|fancy-canvas/.test(msg) ||
    /Object is disposed/.test(msg) ||
    /HTMLCanvasElement's getContext/.test(msg)
  );
};

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/tests/setup.ts"],
    include: ["src/**/__tests__/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "lcov"],
      include: ["src/lib/**", "src/stores/**"],
      exclude: ["src/**/__tests__/**", "src/tests/**"],
    },
    restoreMocks: true,
    // Swallow lightweight-charts internal teardown noise. The animation-frame
    // paint fires after our afterEach removes its own handlers, and the
    // chart tries to paint against a disposed canvas in jsdom (no
    // HTMLCanvasElement.getContext backing). The test assertions all pass;
    // this just keeps the vitest "Unhandled Errors" panel clean.
    onUnhandledError(err) {
      if (isLightweightChartsNoise(err)) return false;
    },
  },
});
