'use client';

import type { ReactNode } from 'react';
import SiteNav, { type NavKey } from './site-nav';

type MaxWidth = 'max-w-6xl' | 'max-w-5xl' | 'max-w-3xl' | 'max-w-[1600px]';

interface Props {
  active?: NavKey;
  extraLabel?: string;
  right?: ReactNode;
  wrap?: boolean;
  maxWidth: MaxWidth;
  children: ReactNode;
}

// PAGE-02 (design.md): every page renders the same shell — skip link ->
// <SiteNav> inside <header> -> exactly one <main id="main"> -> the global
// footer disclaimer (already rendered once, globally, by app/layout.tsx, so
// it isn't repeated here). Delivers the skip link (A11Y-12) and the header/
// nav/main landmarks (A11Y-13) for free, so no page can get it wrong or
// forget it.
export default function PageShell({ active, extraLabel, right, wrap, maxWidth, children }: Props) {
  return (
    <div className="min-h-screen bg-bg text-tx">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50
                   focus:px-4 focus:py-2 focus:rounded-lg focus:bg-accent focus:text-bg
                   focus:text-sm focus:font-semibold"
      >
        Skip to content
      </a>
      <header className={`${maxWidth} mx-auto px-4 pt-8`}>
        <SiteNav active={active} extraLabel={extraLabel} right={right} wrap={wrap} />
      </header>
      <main id="main" className={`${maxWidth} mx-auto px-4 pb-16`}>
        {children}
      </main>
    </div>
  );
}
