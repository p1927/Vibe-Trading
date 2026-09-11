/**
 * REST API origin for `fetch` calls.
 *
 * - Vite dev server (dev 5899 / release 5909 / bare 5173): talk directly to that tier's API.
 *   A same-origin call to a prefix missing from `vite.config.ts`'s proxy silently returns the
 *   SPA's `index.html` with a 200 and `text/html`, and the caller fails parsing JSON rather than
 *   seeing an error (`/board` shipped that way). The proxy now covers every prefix the client
 *   calls, and `src/__tests__/viteProxy.test.ts` fails if a new one is added without an entry;
 *   the absolute origin here is kept as defence in depth.
 * - Combined server (8899/8909): same-origin relative paths.
 *
 * The release UI port pair is listed explicitly rather than inferred, mirroring
 * `stack/ports.yaml`'s `host_port`/`release_host_port` split. Before this, only the dev pair
 * was mapped, so the release frontend fell through to same-origin and every `/board/*` call
 * — the whole Agent and Advisory boards — resolved to the SPA fallback instead of the API.
 */
const UI_PORT_TO_API_PORT: Record<string, string> = {
  "5899": "8899", // dev  — stack/ports.yaml vibe_frontend -> vibe_backend
  "5909": "8909", // release — the same pair's release_host_port values
  "5173": "8899", // bare `vite dev` default, points at the dev API
};

export function resolveApiBase(): string {
  if (typeof window === "undefined") return "";

  const { protocol, hostname, port } = window.location;
  const apiPort = UI_PORT_TO_API_PORT[port];
  if (apiPort) {
    return `${protocol}//${hostname}:${apiPort}`;
  }
  return "";
}
