'use client';

import { useEffect, useRef, useState, Suspense } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import ProgressTracker  from '@/components/progress-tracker';
import ResultsDashboard from '@/components/results-dashboard';
import SiteNav          from '@/components/site-nav';
import { useStockAnalysis } from '@/lib/useStockAnalysis';

const MAX_SYMBOLS = 2;

function parseSymbols(raw: string): string[] {
  const seen = new Set<string>();
  const symbols: string[] = [];
  for (const part of raw.split(',')) {
    const sym = part.trim().toUpperCase().replace(/[^A-Z0-9&-]/g, '');
    if (sym && !seen.has(sym)) {
      seen.add(sym);
      symbols.push(sym);
    }
    if (symbols.length === MAX_SYMBOLS) break;
  }
  return symbols;
}

function CompareColumn({ symbol }: { symbol: string }) {
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

  return (
    <div className="w-full 2xl:flex-1 2xl:min-w-0 min-w-0">
      <div className="flex items-center gap-2 mb-4">
        <Link
          href={`/?symbol=${symbol}`}
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

  useEffect(() => {
    setInputValue(urlSymbols.join(', '));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams.get('symbols')]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const symbols = parseSymbols(inputValue);
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

        <form onSubmit={handleSubmit} className="flex items-center gap-2 mb-8 max-w-md">
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

        {urlSymbols.length === 0 ? (
          <div className="rounded-xl border border-border bg-card px-6 py-16 text-center">
            <p className="text-sm text-muted">
              Enter two comma-separated tickers above to compare them side by side.
            </p>
          </div>
        ) : (
          <div className="flex flex-col 2xl:flex-row gap-8 items-start">
            {urlSymbols.map(sym => (
              <CompareColumn key={sym} symbol={sym} />
            ))}
          </div>
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
