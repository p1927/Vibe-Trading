import { isRouteErrorResponse, useRouteError, Link } from "react-router-dom";
import { AlertTriangle, RefreshCw } from "lucide-react";

export function RouteErrorFallback() {
  const error = useRouteError();
  let title = "Something went wrong";
  let detail = "This page failed to load. Try refreshing or return home.";

  if (isRouteErrorResponse(error)) {
    title = `${error.status} ${error.statusText}`;
    detail = error.data?.message || detail;
  } else if (error instanceof Error) {
    detail = error.message;
  }

  const isChunkError =
    detail.includes("Failed to fetch dynamically imported module") ||
    detail.includes("Importing a module script failed");

  return (
    <div className="mx-auto flex min-h-[60vh] max-w-lg flex-col items-center justify-center gap-4 p-6 text-center">
      <div className="inline-flex h-12 w-12 items-center justify-center rounded-full border border-destructive/30 bg-destructive/5 text-destructive">
        <AlertTriangle className="h-6 w-6" />
      </div>
      <div>
        <h1 className="text-lg font-semibold text-foreground">{title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{detail}</p>
        {isChunkError && (
          <p className="mt-2 text-xs text-muted-foreground">
            Dev server may be restarting — wait a moment, then reload.
          </p>
        )}
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-medium hover:bg-muted"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Reload page
        </button>
        <Link to="/" className="rounded-lg border px-3 py-1.5 text-sm font-medium hover:bg-muted">
          Go home
        </Link>
      </div>
    </div>
  );
}
