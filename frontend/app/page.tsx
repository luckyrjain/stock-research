'use client';

import { useRef, useEffect, useState, Suspense } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import TickerSearch     from '@/components/ticker-search';
import ProgressTracker  from '@/components/progress-tracker';
import ResultsDashboard from '@/components/results-dashboard';
import SiteNav          from '@/components/site-nav';
import { useStockAnalysis } from '@/lib/useStockAnalysis';

// Matches api.py's _is_isin(). A deep-linked ISIN (used for BSE SME stocks,
// whose own scrip code isn't a directly analyzable ticker — see
// sme-signals/page.tsx) has to be resolved to a real ticker via
// /api/validate first, the same resolution ticker-search.tsx already does
// for user-typed ISINs — /api/analyse expects an actual ticker, not an ISIN.
const ISIN_RE = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;

function HomePageInner() {
  const {
    phase, taskStatus, report, error, currentSymbol,
    isRunning, isIdle,
    handleAnalyse, handleHardRefresh,
  } = useStockAnalysis();

  const searchParams = useSearchParams();
  // Tracks the last `?symbol=` value this effect actually acted on — not a
  // one-shot boolean, since that would only ever fire for the very first
  // deep link on mount. A report page stays mounted at `/` and several
  // in-app links point at a *new* `/?symbol=` while it's already showing a
  // report (Similar Stocks rail, ConsolidatedCard's "View full analysis",
  // SME/screener deep links) — each of those needs to re-trigger analysis,
  // not be silently ignored because *some* symbol was already deep-linked.
  const lastDeepLinkedSymbol = useRef<string | null>(null);
  const [resolving, setResolving] = useState(false);
  const [resolveError, setResolveError] = useState<string | null>(null);

  // Deep link: /?symbol=TCS auto-starts analysis (used by SME signals page links)
  useEffect(() => {
    const sym = searchParams.get('symbol')?.toUpperCase();
    if (!sym || sym === lastDeepLinkedSymbol.current) return;
    lastDeepLinkedSymbol.current = sym;

    if (!ISIN_RE.test(sym)) {
      handleAnalyse(sym);
      return;
    }

    setResolving(true);
    fetch(`/api/validate/${encodeURIComponent(sym)}`)
      .then(res => res.json())
      .then((data: { valid?: boolean; symbol?: string }) => {
        // Stale-response guard: the user may have already navigated to a
        // *different* `/?symbol=` (e.g. a Similar Stocks link needing no
        // resolution, so it wins the race) before this validate call
        // returns — `lastDeepLinkedSymbol` no longer being `sym` means a
        // newer deep link has since superseded this one, so don't clobber
        // whatever's now on screen with this stale result.
        if (lastDeepLinkedSymbol.current !== sym) return;
        if (data.valid && data.symbol) {
          handleAnalyse(data.symbol);
        } else {
          setResolveError(`Couldn't resolve ${sym} to an analyzable ticker.`);
        }
      })
      .catch(() => {
        if (lastDeepLinkedSymbol.current === sym) setResolveError(`Couldn't resolve ${sym} to an analyzable ticker.`);
      })
      .finally(() => {
        if (lastDeepLinkedSymbol.current === sym) setResolving(false);
      });
  }, [searchParams, handleAnalyse]);

  return (
    <main className="min-h-screen bg-bg text-tx">
      <div className="max-w-5xl mx-auto px-4 pt-8 pb-16">

        {/* Nav is always mounted, idle or not — a first-time visitor
            previously couldn't sign in or discover Screener/Watchlist/
            Portfolio/Compare at all until they'd already run an analysis,
            since only the non-idle branch rendered it. */}
        <SiteNav />

        {isIdle ? (
          <div className="max-w-2xl mx-auto pt-8">
            <div className="mb-12 text-center">
              <h1 className="text-4xl font-black tracking-tight text-tx mb-2">
                Alpha<span className="text-accent">Pulse</span>
              </h1>
              <p className="text-muted text-sm">AI-powered equity research for Indian markets</p>
              <p className="text-muted/60 text-xs mt-3">
                Research a stock you know, discover new ones, or catch technical breakouts —
                three ways into the same data.
              </p>
              <div className="flex items-center justify-center gap-3 mt-4 flex-wrap">
                <Link
                  href="/market-picks"
                  className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-full
                             bg-accent/10 border border-accent/20 text-accent text-xs font-semibold
                             hover:bg-accent/20 transition-colors"
                >
                  This week's top picks →
                </Link>
                <Link
                  href="/sme-signals"
                  className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-full
                             bg-accent/10 border border-accent/20 text-accent text-xs font-semibold
                             hover:bg-accent/20 transition-colors"
                >
                  SME golden cross screener →
                </Link>
              </div>
            </div>

            {resolving && (
              <p className="text-center text-muted text-xs mb-4">Resolving listing…</p>
            )}
            {resolveError && (
              <div className="mb-6 px-5 py-4 rounded-xl bg-sell/10 border border-sell/30 text-sell text-sm text-center">
                {resolveError}
              </div>
            )}

            <TickerSearch onAnalyse={handleAnalyse} disabled={isRunning} />

            {/* A skeptical first-time visitor previously had to commit a real
                ticker and wait through the full multi-stage pipeline before
                seeing any payoff at all. This runs a real analysis for a
                well-known large-cap on one click — the exact same live
                pipeline every other query goes through, never a fabricated
                mock report, which would risk being mistaken for a real
                recommendation on a product whose whole premise is trustworthy
                data. */}
            <p className="text-center text-muted/50 text-xs mt-4">
              New here?{' '}
              <button
                type="button"
                onClick={() => handleAnalyse('TCS')}
                disabled={isRunning}
                className="text-accent hover:underline font-medium disabled:opacity-50 disabled:no-underline"
              >
                See a real report for TCS →
              </button>
            </p>
          </div>
        ) : (
          <>
            <TickerSearch onAnalyse={handleAnalyse} disabled={isRunning} compact />

            {(phase === 'fetching' || phase === 'analysing') && (
              <ProgressTracker taskStatus={taskStatus} phase={phase} />
            )}

            {phase === 'error' && error && (
              <div className="mb-8 px-5 py-4 rounded-xl bg-sell/10 border border-sell/30 text-sell text-sm flex items-start justify-between gap-4">
                <span>{error}</span>
                {currentSymbol && (
                  <button
                    onClick={() => handleAnalyse(currentSymbol)}
                    className="shrink-0 px-3 py-1 rounded-lg text-xs font-semibold
                      border border-sell/40 text-sell hover:bg-sell/10
                      transition-colors duration-150"
                  >
                    Try Again
                  </button>
                )}
              </div>
            )}

            {phase === 'done' && report && (
              <ResultsDashboard report={report} onHardRefresh={handleHardRefresh} />
            )}
          </>
        )}

      </div>
    </main>
  );
}

export default function HomePage() {
  return (
    <Suspense fallback={null}>
      <HomePageInner />
    </Suspense>
  );
}
