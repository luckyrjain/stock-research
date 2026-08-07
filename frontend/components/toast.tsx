'use client';

import { createContext, useCallback, useContext, useRef, useState } from 'react';

interface ToastItem {
  id: number;
  message: string;
}

interface ToastContextValue {
  showError: (message: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);
const DISMISS_MS = 6000;

/** Floating error notifications for failures that don't warrant taking over
 * the page (a background mutation like a watchlist toggle) — the persistent
 * inline Error banner (design.md §5) stays for failures that block a whole
 * page's content. Reuses that banner's exact tone/classes, just floated,
 * stacked, and auto-dismissing. */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const idRef = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const showError = useCallback((message: string) => {
    const id = ++idRef.current;
    setToasts(prev => [...prev, { id, message }]);
    setTimeout(() => dismiss(id), DISMISS_MS);
  }, [dismiss]);

  return (
    <ToastContext.Provider value={{ showError }}>
      {children}
      <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-2 w-full max-w-sm pointer-events-none">
        {toasts.map(t => (
          <div
            key={t.id}
            role="alert"
            className="pointer-events-auto animate-fade-up flex items-start justify-between gap-3
                       px-5 py-4 rounded-xl bg-sell/10 border border-sell/30 text-sell text-sm
                       shadow-2xl shadow-black/60"
          >
            <span>{t.message}</span>
            <button
              onClick={() => dismiss(t.id)}
              aria-label="Dismiss"
              className="shrink-0 text-sell/70 hover:text-sell transition-colors"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/** No-op fallback if called outside ToastProvider — layout.tsx always mounts
 * one, so this only guards stray/test usage. */
export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  return ctx ?? { showError: () => {} };
}
