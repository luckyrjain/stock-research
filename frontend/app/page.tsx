'use client';

import { useRef, useEffect, Suspense } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import TickerSearch     from '@/components/ticker-search';
import ProgressTracker  from '@/components/progress-tracker';
import ResultsDashboard from '@/components/results-dashboard';
import HeaderSearch     from '@/components/header-search';
import { useStockAnalysis } from '@/lib/useStockAnalysis';

function HomePageInner() {
  const {
    phase, taskStatus, report, error, currentSymbol,
    isRunning, isIdle,
    handleAnalyse, handleHardRefresh,
  } = useStockAnalysis();

  const searchParams = useSearchParams();
  const deepLinkDone = useRef(false);

  // Deep link: /?symbol=TCS auto-starts analysis (used by SME signals page links)
  useEffect(() => {
    const sym = searchParams.get('symbol');
    if (sym && !deepLinkDone.current) {
      deepLinkDone.current = true;
      handleAnalyse(sym.toUpperCase());
    }
  }, [searchParams, handleAnalyse]);

  return (
    <main className="min-h-screen bg-bg text-tx">
      <div className={`max-w-5xl mx-auto px-4 ${isIdle ? 'py-16' : 'pt-8 pb-16'}`}>

        {isIdle ? (
          <div className="max-w-2xl mx-auto">
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
                  📈 This week's top picks →
                </Link>
                <Link
                  href="/sme-signals"
                  className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-full
                             bg-accent/10 border border-accent/20 text-accent text-xs font-semibold
                             hover:bg-accent/20 transition-colors"
                >
                  ⚡ SME golden cross screener →
                </Link>
              </div>
            </div>
            <TickerSearch onAnalyse={handleAnalyse} disabled={isRunning} />
          </div>
        ) : (
          <>
            <div className="flex items-center gap-4 mb-5 pb-4 border-b border-border">
              <span className="text-base font-black tracking-tight text-tx">
                Alpha<span className="text-accent">Pulse</span>
              </span>
              <Link
                href="/market-picks"
                className="text-xs font-semibold text-muted hover:text-accent transition-colors"
              >
                Market Picks →
              </Link>
              <Link
                href="/sme-signals"
                className="text-xs font-semibold text-muted hover:text-accent transition-colors"
              >
                SME Signals →
              </Link>
              <Link
                href="/watchlist"
                className="text-xs font-semibold text-muted hover:text-accent transition-colors"
              >
                Watchlist →
              </Link>
              <Link
                href="/compare"
                className="text-xs font-semibold text-muted hover:text-accent transition-colors"
              >
                Compare →
              </Link>
              <div className="ml-auto flex items-center gap-3">
                <HeaderSearch />
              </div>
            </div>

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
