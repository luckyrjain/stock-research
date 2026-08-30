'use client';

import { useId, useRef, useState } from 'react';
import { useFocusTrap } from '@/lib/use-focus-trap';

interface Props {
  title: string;
  children: React.ReactNode;
  className?: string;
  align?: 'left' | 'center';
}

// Small "ⓘ" popover for explaining scores/thresholds inline, per design.md's
// popover pattern (fixed-inset backdrop + absolute panel).
export default function InfoTooltip({ title, children, className = '', align = 'center' }: Props) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const panelRef = useRef<HTMLDivElement>(null);

  // Same inert-background + Tab-wrap + Escape treatment as ConsolidatedCard
  // and SourcesPopover (A11Y-11) — this dialog's content is caller-supplied
  // and can hold links, so it gets the full trap rather than Escape-only.
  useFocusTrap(panelRef, open, { onEscape: () => setOpen(false) });

  return (
    <span className={`relative inline-flex ${className}`}>
      {/* Visual circle stays 14x14 (w-3.5 h-3.5); the button itself is
          unsized and padded out to a 44px hit area with a canceling negative
          margin (A11Y-14) — padding on the fixed-size circle itself would
          shrink its content box under border-box sizing instead of growing
          the hit area, so the padding has to live on this wrapping button
          and the hover state has to reach the inner circle via group-hover. */}
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-label={`About ${title}`}
        aria-expanded={open}
        aria-controls={panelId}
        className="group p-3 -m-3 shrink-0 flex items-center justify-center"
      >
        <span
          aria-hidden="true"
          className="w-3.5 h-3.5 rounded-full border border-muted/40 text-muted/70 text-[9px] font-bold leading-none
                     flex items-center justify-center transition-colors
                     group-hover:text-tx group-hover:border-muted"
        >
          i
        </span>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div
            ref={panelRef}
            id={panelId}
            role="dialog"
            aria-label={title}
            tabIndex={-1}
            className={`absolute top-full mt-2 z-20 w-64 bg-card border border-border rounded-xl
                        shadow-2xl shadow-black/60 p-3
                        ${align === 'center' ? 'left-1/2 -translate-x-1/2' : 'left-0'}`}
          >
            <p className="text-[11px] font-bold text-tx mb-1.5">{title}</p>
            <div className="text-[11px] text-muted leading-relaxed space-y-1">{children}</div>
          </div>
        </>
      )}
    </span>
  );
}
