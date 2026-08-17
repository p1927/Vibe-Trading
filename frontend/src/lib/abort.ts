/**
 * Detect whether a thrown value is an AbortError (the DOMException that
 * `fetch()` rejects with when its `AbortSignal` is triggered).
 *
 * `request()` in `./api.ts` re-throws the raw DOMException when an
 * outer's signal aborts (vs the internal timeout signal). Three callers
 * need to recognise that case without depending on the runtime having
 * `DOMException` as a constructor name:
 *
 *   - `Runtime.tsx` — polling-driven `controller.abort()` on refresh /
 *     unmount must not look like a runtime crash.
 *   - `main.tsx` `unhandledrejection` listener — aborts are normal
 *     lifecycle, not crashes, and must NOT post a `{type:"iframe-error",
 *     source:"vibe", message:"signal is aborted without reason"}` to
 *     the parent shell (which would render "Vibe crashed").
 *   - `ErrorBoundary.tsx` `componentDidCatch` — aborts that bubble to a
 *     render-phase boundary must not be reported as crashes either.
 *
 * The previous behaviour shipped a banner on every 15 s runtime-tab poll
 * because `Runtime.tsx` `.catch(...)` re-threw on abort and the outer
 * `try/catch` couldn't attach in time, leaving a one-tick window where
 * the rejection became an `unhandledrejection`.
 */
export function isAbortError(err: unknown): boolean {
  if (!err) return false;
  // Standard DOMException with name "AbortError" — fetch() with an aborted
  // signal rejects with this in all evergreen browsers.
  if (typeof DOMException !== "undefined" && err instanceof DOMException) {
    return err.name === "AbortError";
  }
  // Fallback for environments without DOMException (older Safari, some
  // test runners): duck-type on name. Anything shaped like { name: "AbortError" }
  // counts — that's what Chrome emits when a fetch's signal is aborted
  // without a custom reason (the literal message is "signal is aborted
  // without reason", but `name` is the stable contract).
  if (typeof err === "object" && "name" in err) {
    return (err as { name?: unknown }).name === "AbortError";
  }
  return false;
}