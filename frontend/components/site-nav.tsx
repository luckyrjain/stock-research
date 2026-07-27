'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import HeaderSearch from './header-search';
import AuthWidget from './auth-widget';
import { useWatchlistAlertsBadge } from '@/lib/watchlist-alerts-badge';

function WatchlistAlertDot() {
  return (
    <span
      aria-label="A watched stock has a same-day recommendation change or price move"
      title="A watched stock has a same-day recommendation change or price move"
      className="w-1.5 h-1.5 rounded-full bg-accent shrink-0"
    />
  );
}

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
//
// Below the `md` breakpoint the 8 pipe-separated links collapse behind a
// hamburger toggle instead of wrapping onto 2-3 lines of small text above
// every page's content — real friction on a phone, and this is an
// India-focused, mobile-heavy product. HeaderSearch/AuthWidget stay visible
// at every width (HeaderSearch's own input is already responsive,
// `w-36 sm:w-56`) since search and account access are worth the space even
// on a small screen.
export default function SiteNav({ active, extraLabel, right, wrap = false }: Props) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  // A same-day recommendation change or notable price move on any watched
  // symbol — previously only visible to a user who happened to be on
  // /watchlist itself. See lib/watchlist-alerts-badge.ts for the full story.
  const hasWatchlistAlerts = useWatchlistAlertsBadge();

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)
          && toggleRef.current && !toggleRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setMenuOpen(false);
        toggleRef.current?.focus();
      }
    }
    document.addEventListener('mousedown', onClickOutside);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onClickOutside);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, []);

  return (
    <div className={`relative flex items-center gap-4 mb-8 pb-4 border-b border-border ${wrap ? 'flex-wrap' : ''}`}>
      <Link href="/" className="text-base font-black tracking-tight text-tx">
        Alpha<span className="text-accent">Pulse</span>
      </Link>

      {/* Desktop: the full pipe-separated link list, unchanged. */}
      <div className="hidden md:contents">
        {LINKS.map(link => (
          <span key={link.key} className="contents">
            <span className="text-border-hi">|</span>
            {active === link.key ? (
              <span className="text-sm font-semibold text-accent whitespace-nowrap inline-flex items-center gap-1">
                {link.label}
                {link.key === 'watchlist' && hasWatchlistAlerts && <WatchlistAlertDot />}
              </span>
            ) : (
              <Link href={link.href} className="text-sm text-muted hover:text-tx transition-colors whitespace-nowrap inline-flex items-center gap-1">
                {link.label}
                {link.key === 'watchlist' && hasWatchlistAlerts && <WatchlistAlertDot />}
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
      </div>

      {/* Mobile: hamburger toggle for the same link set, collapsed. */}
      <button
        ref={toggleRef}
        onClick={() => setMenuOpen(o => !o)}
        aria-haspopup="true"
        aria-expanded={menuOpen}
        aria-controls="site-nav-mobile-menu"
        aria-label="Toggle navigation menu"
        className="md:hidden flex items-center justify-center w-8 h-8 rounded-lg border border-border text-muted hover:text-tx transition-colors"
      >
        <span aria-hidden="true" className="text-sm">{menuOpen ? '✕' : '☰'}</span>
      </button>
      {extraLabel && (
        <span className="md:hidden text-sm font-semibold text-accent truncate">{extraLabel}</span>
      )}

      <div className="ml-auto flex items-center gap-3">
        <HeaderSearch />
        <AuthWidget />
        {right}
      </div>

      {menuOpen && (
        <div
          ref={menuRef}
          id="site-nav-mobile-menu"
          role="menu"
          className="md:hidden absolute left-0 top-full mt-1 w-56 rounded-lg bg-card border border-border shadow-lg py-1 z-20"
        >
          {LINKS.map(link => (
            active === link.key ? (
              <span key={link.key} role="menuitem" className="px-3 py-2 text-sm font-semibold text-accent flex items-center gap-1.5">
                {link.label}
                {link.key === 'watchlist' && hasWatchlistAlerts && <WatchlistAlertDot />}
              </span>
            ) : (
              <Link
                key={link.key}
                href={link.href}
                role="menuitem"
                onClick={() => setMenuOpen(false)}
                className="px-3 py-2 text-sm text-muted hover:text-tx hover:bg-surface/60 transition-colors flex items-center gap-1.5"
              >
                {link.label}
                {link.key === 'watchlist' && hasWatchlistAlerts && <WatchlistAlertDot />}
              </Link>
            )
          ))}
        </div>
      )}
    </div>
  );
}
