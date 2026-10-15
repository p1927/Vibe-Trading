import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";

export interface ConfirmOptions {
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "primary" | "destructive";
}

type Pending = ConfirmOptions & { resolve: (ok: boolean) => void };

/**
 * The one way to ask "are you sure?" from an event handler (D306: window.confirm is banned, ConfirmDialog is
 * the only confirm UI). `const [confirm, dialog] = useConfirm()`, render `{dialog}` once, then
 * `if (!(await confirm({ title, description }))) return;` - resolves true on confirm, false on cancel/Escape.
 * Asking again while one is open cancels the first (false), so two prompts can never both be answered by one click.
 */
export function useConfirm(): readonly [(options: ConfirmOptions) => Promise<boolean>, ReactNode] {
  const [pending, setPending] = useState<Pending | null>(null);
  const current = useRef<Pending | null>(null);

  const settle = useCallback((ok: boolean) => {
    const open = current.current;
    current.current = null;
    setPending(null);
    open?.resolve(ok);
  }, []);

  const confirm = useCallback((options: ConfirmOptions) => {
    current.current?.resolve(false);
    return new Promise<boolean>((resolve) => {
      const next = { ...options, resolve };
      current.current = next;
      setPending(next);
    });
  }, []);

  // An unmounted page must not leave a handler awaiting forever.
  useEffect(() => () => current.current?.resolve(false), []);

  const dialog = (
    <ConfirmDialog
      open={pending !== null}
      title={pending?.title ?? ""}
      description={pending?.description}
      confirmLabel={pending?.confirmLabel ?? "Confirm"}
      cancelLabel={pending?.cancelLabel ?? "Cancel"}
      tone={pending?.tone}
      onConfirm={() => settle(true)}
      onCancel={() => settle(false)}
    />
  );
  return [confirm, dialog] as const;
}
