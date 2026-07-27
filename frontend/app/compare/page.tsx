'use client';

import { useCallback, useEffect, useRef, useState, Suspense } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import ProgressTracker    from '@/components/progress-tracker';
import ResultsDashboard   from '@/components/results-dashboard';
import SiteNav            from '@/components/site-nav';
import CompareDiffTable   from '@/components/compare-diff-table';
import { useStockAnalysis } from '@/lib/useStockAnalysis';
import type { Report } from '@/types';

const MAX_SYMBOLS = 2;

// No MAX_SYMBOLS cap — the raw, deduped ticker list a user typed/pasted or
// put in a shared link. parseSymbols() below is the capped view every
// other call site actually uses; this one exists only so the URL can carry
// the user's full input and the page can tell whether parseSymbols()
// dropped something, instead of the capped URL silently discarding it at
// the point of submission and leaving no way to detect the drop later.
function parseAllSymbols(raw: string): string[] {
  const seen = new Set<string>();
  const symbols: string[] = [];
  for (const part of raw.split(',')) {
    const sym = part.trim().toUpperCase().replace(/[^A-Z0-9&-]/g, '');
    if (sym && !seen.has(sym)) {
      seen.add(sym);
      symbols.push(sym);
    }
  }
  return symbols;
}

function parseSymbols(raw: string): string[] {
  return parseAllSymbols(raw).slice(0, MAX_SYMBOLS);
}

function CompareColumn({ symbol, onReport }: { symbol: string; onReport: (symbol: string, report: Report | null) => void }) {
  const {
    phase, taskStatus, report, error,
    handleAnalyse, handleHardRefresh,
  } = useStockAnalysis();
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    handleAnalyse(symbol);
  }, [symbol, handleAnalyse]);

  // Lifts the finished report up to the parent for the head-to-head diff
  // table — this column still owns its own SSE fetch/progress state
  // entirely; the parent never re-fetches anything, it just reads the result.
  useEffect(() => {
    onReport(symbol, report);
  }, [symbol, report, onReport]);

  return (
    <div className="w-full 2xl:flex-1 2xl:min-w-0 min-w-0">
      <div className="flex items-center gap-2 mb-4">
        <Link
          href={`/?symbol=${encodeURIComponent(symbol)}`}
          className="font-mono font-black text-lg text-tx hover:text-accent transition-colors"
        >
          {symbol}
        </Link>
      </div>

      {(phase === 'fetching' || phase === 'analysing') && (
        <ProgressTracker taskStatus={taskStatus} phase={phase} />
      )}

      {phase === 'error' && error && (
        <div className="mb-4 px-4 py-3 rounded-xl bg-sell/10 border border-sell/30 text-sell text-sm flex items-start justify-between gap-4">
          <span>{error}</span>
          <button
            onClick={() => handleAnalyse(symbol)}
            className="shrink-0 px-3 py-1 rounded-lg text-xs font-semibold
              border border-sell/40 text-sell hover:bg-sell/10 transition-colors duration-150"
          >
            Try Again
          </button>
        </div>
      )}

      {phase === 'done' && report && (
        <ResultsDashboard report={report} onHardRefresh={handleHardRefresh} />
      )}
    </div>
  );
}

function ComparePageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const urlSymbols = parseSymbols(searchParams.get('symbols') ?? '');

  const [inputValue, setInputValue] = useState(urlSymbols.join(', '));
  const [reports, setReports] = useState<Record<string, Report | null>>({});
  // Derived from the URL's full raw symbol list (not form-submit-only
  // state) so this also covers a direct/shared link with more than
  // MAX_SYMBOLS tickers in it, not just typing them into the form on this
  // page. Requires handleSubmit below to push the *un*-capped list into
  // the URL — otherwise the cap would already have discarded the extra
  // ticker before this could ever see it.
  const truncatedCount = Math.max(0, parseAllSymbols(searchParams.get('symbols') ?? '').length - urlSymbols.length);

  useEffect(() => {
    setInputValue(urlSymbols.join(', '));
    setReports({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams.get('symbols')]);

  // Stable across renders (empty dep array), so CompareColumn's own
  // `[symbol, report, onReport]` effect only re-fires when its report
  // actually changes. The Object.is bail-out is cheap extra safety against
  // ever setting `reports` to an equivalent-but-new object.
  const handleReport = useCallback((symbol: string, report: Report | null) => {
    setReports(prev => (prev[symbol] === report ? prev : { ...prev, [symbol]: report }));
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // Pushes the full (uncapped) list — urlSymbols above still applies
    // MAX_SYMBOLS via parseSymbols() for actual rendering/fetching, but the
    // URL needs the un-truncated list so truncatedCount can detect a drop.
    const symbols = parseAllSymbols(inputValue);
    router.push(symbols.length ? `/compare?symbols=${encodeURIComponent(symbols.join(','))}` : '/compare');
  };

  return (
    <main className="min-h-screen bg-bg text-tx">
      <div className="max-w-[1600px] mx-auto px-4 pt-8 pb-16">

        <SiteNav active="compare" wrap />

        <div className="mb-6">
          <h1 className="text-xl font-black tracking-tight text-tx mb-1.5">Compare Stocks</h1>
          <p className="text-muted text-sm max-w-xl leading-relaxed">
            Two full stock analysis reports, side by side. Each runs the same pipeline you&apos;d
            get from analysing them one at a time.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="flex items-center gap-2 mb-2 max-w-md">
          <input
            value={inputValue}
            onChange={e => setInputValue(e.target.value)}
            placeholder="e.g. TCS, INFY"
            maxLength={50}
            spellCheck={false}
            aria-label="Tickers to compare, comma-separated"
            className="flex-1 bg-card border border-border rounded-lg px-3 py-2 text-sm font-mono
                       uppercase tracking-wide text-tx placeholder:text-muted placeholder:normal-case
                       placeholder:font-sans placeholder:tracking-normal outline-none
                       focus:border-accent transition-colors"
          />
          <button
            type="submit"
            className="px-4 py-2 rounded-lg text-sm font-semibold bg-accent text-white
                       hover:opacity-90 transition-opacity"
          >
            Compare
          </button>
        </form>

        {truncatedCount > 0 && (
          <p className="text-xs text-hold mb-6">
            Only the first {MAX_SYMBOLS} tickers are compared — {truncatedCount} more {truncatedCount === 1 ? 'was' : 'were'} dropped.
          </p>
        )}

        {urlSymbols.length === 0 ? (
          <div className="rounded-xl border border-border bg-card px-6 py-16 text-center">
            <p className="text-sm text-muted">
              Enter two comma-separated tickers above to compare them side by side.
            </p>
          </div>
        ) : (
          <>
            {urlSymbols.length === MAX_SYMBOLS && reports[urlSymbols[0]] && reports[urlSymbols[1]] && (
              <CompareDiffTable reportA={reports[urlSymbols[0]]!} reportB={reports[urlSymbols[1]]!} />
            )}
            <div className="flex flex-col 2xl:flex-row gap-8 items-start">
              {urlSymbols.map(sym => (
                <CompareColumn key={sym} symbol={sym} onReport={handleReport} />
              ))}
            </div>
          </>
        )}
      </div>
    </main>
  );
}

export default function ComparePage() {
  return (
    <Suspense fallback={null}>
      <ComparePageInner />
    </Suspense>
  );
}
