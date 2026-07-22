'use client';

import { useState } from 'react';

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

  return (
    <span className={`relative inline-flex ${className}`}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-label={`About ${title}`}
        aria-expanded={open}
        className="w-3.5 h-3.5 rounded-full border border-muted/40 text-muted/70 text-[9px] font-bold leading-none
                   flex items-center justify-center hover:text-tx hover:border-muted transition-colors shrink-0"
      >
        i
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div
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
