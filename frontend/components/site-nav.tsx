'use client';

import Link from 'next/link';
import HeaderSearch from './header-search';
import AuthWidget from './auth-widget';

export type NavKey =
  | 'market-picks'
  | 'sme-signals'
  | 'screener'
  | 'track-record'
  | 'watchlist'
  | 'portfolio'
  | 'compare'
  | 'api-keys';

const LINKS: { key: NavKey; href: string; label: string }[] = [
  { key: 'market-picks', href: '/market-picks',         label: 'Market Picks' },
  { key: 'sme-signals',  href: '/sme-signals',           label: 'SME Signals' },
  { key: 'screener',     href: '/screener',              label: 'Screener' },
  { key: 'track-record', href: '/market-picks/history',  label: 'Track Record' },
  { key: 'watchlist',    href: '/watchlist',              label: 'Watchlist' },
  { key: 'portfolio',    href: '/portfolio',              label: 'Portfolio' },
  { key: 'compare',      href: '/compare',                label: 'Compare' },
  // A signed-out visitor previously had no navigational path to discover the
  // API exists at all — it was reachable only through the account dropdown
  // once signed in. Visible to everyone (like every other link here); the
  // page itself already handles the signed-out state with its own prompt.
  { key: 'api-keys',     href: '/api-keys',               label: 'API Keys' },
];

interface Props {
  /** Which link (if any) represents the current page — highlighted, not a link. */
  active?: NavKey;
  /** Current-page label for a page that isn't part of the main link set (e.g. "API Keys"). */
  extraLabel?: string;
  /** Extra page-specific controls (refresh/cancel buttons) rendered after AuthWidget. */
  right?: React.ReactNode;
  /** Allow the bar to wrap on narrow viewports — pages with a `right` slot need this. */
  wrap?: boolean;
}

// Single source of truth for the nav bar every secondary page repeats — see
// the frontend design review's "nav bar duplicated across eight pages" finding.
// Previously each page hand-copied this markup, which had already drifted
// (the home page's own non-idle nav was missing Portfolio/Track Record links
// every other page carried).
export default function SiteNav({ active, extraLabel, right, wrap = false }: Props) {
  return (
    <div className={`flex items-center gap-4 mb-8 pb-4 border-b border-border ${wrap ? 'flex-wrap' : ''}`}>
      <Link href="/" className="text-base font-black tracking-tight text-tx">
        Alpha<span className="text-accent">Pulse</span>
      </Link>
      {LINKS.map(link => (
        <span key={link.key} className="contents">
          <span className="text-border-hi">|</span>
          {active === link.key ? (
            <span className="text-sm font-semibold text-accent whitespace-nowrap">{link.label}</span>
          ) : (
            <Link href={link.href} className="text-sm text-muted hover:text-tx transition-colors whitespace-nowrap">
              {link.label}
            </Link>
          )}
        </span>
      ))}
      {extraLabel && (
        <>
          <span className="text-border-hi">|</span>
          <span className="text-sm font-semibold text-accent">{extraLabel}</span>
        </>
      )}
      <div className="ml-auto flex items-center gap-3">
        <HeaderSearch />
        <AuthWidget />
        {right}
      </div>
    </div>
  );
}
