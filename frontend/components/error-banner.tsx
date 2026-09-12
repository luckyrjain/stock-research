'use client';

// Generic UI atoms shared across most pages — extracted for the same
// "byte-identical (or near-identical) duplication" reason data-table-ui.tsx's
// Skeleton was: docs/design.md's "Error banner" pattern and the
// loading-spinner glyph were each hand-copied at many call sites and had
// already drifted (see ErrorBanner below).

export function ErrorBanner({ message, onRetry, retryLabel = 'Retry', className }: {
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
}) {
  // role="alert" is unconditional here — a disclosed accessibility fix. Six
  // of the thirteen inline copies this replaces had silently dropped it.
  return (
    <div
      role="alert"
      className={`px-5 py-4 rounded-xl bg-sell/10 border border-sell/30 text-sell text-sm
        ${onRetry ? 'flex items-start justify-between gap-4' : ''} ${className ?? ''}`}
    >
      <span>{message}</span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="shrink-0 px-3 py-1 rounded-lg text-xs font-semibold
            border border-sell/40 hover:bg-sell/10 transition-colors"
        >
          {retryLabel}
        </button>
      )}
    </div>
  );
}

export function SpinIcon({ className }: { className?: string }) {
  return (
    <span aria-hidden="true" className={`animate-spin-slow ${className ?? ''}`}>⟳</span>
  );
}
