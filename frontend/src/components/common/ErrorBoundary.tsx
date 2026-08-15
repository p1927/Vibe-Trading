import i18n from "@/i18n";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

interface Props { children: ReactNode; fallback?: ReactNode; }
interface State { hasError: boolean; error?: Error; }

// Forward an in-iframe error to the parent Trade UI shell so the shell's
// tab badge + inline banner can reflect it. The shell validates event.origin
// against the iframe's data-origin, so "null" (sandboxed) is allowed but
// arbitrary origins are dropped. Posting with targetOrigin "*" is fine here
// because the shell authenticates the sender, not the receiver.
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
        url: url || (typeof location !== "undefined" ? location.href : ""),
      },
      "*",
    );
  } catch {
    // postMessage can throw in some sandboxed contexts; nothing to do.
  }
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Best-effort report to the parent shell. The shell keeps a per-app
    // banner; we don't throw if the report fails (e.g. cross-origin
    // sandbox, no parent). The console.error below preserves the original
    // diagnostic for browser devtools.
    reportIframeError(
      error?.message || "Unknown render error",
      `${error?.stack || ""}\nComponent stack:\n${info?.componentStack || ""}`,
    );
    console.error("[vibe] ErrorBoundary caught", error, info);
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback ?? (
        <div className="flex items-center gap-2 p-4 rounded-lg border border-destructive/30 bg-destructive/5 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>{this.state.error?.message || i18n.t("errorBoundary.somethingWrong")}</span>
        </div>
      );
    }
    return this.props.children;
  }
}
