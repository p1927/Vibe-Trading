/**
 * REST API origin for `fetch` calls.
 *
 * - Vite dev (5899/5173): talk directly to the API on 8899 (avoids proxy gaps).
 * - Combined server (8899): same-origin relative paths.
 */
export function resolveApiBase(): string {
  if (typeof window === "undefined") return "";

  const { protocol, hostname, port } = window.location;
  if (port === "5899" || port === "5173") {
    return `${protocol}//${hostname}:8899`;
  }
  return "";
}
