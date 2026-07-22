'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import Link from 'next/link';
import type { SmeSignalsResponse } from '@/types';

// ── Filter types ──────────────────────────────────────────────────────────────

type Lookback  = 1 | 3 | 5 | 10;
type Direction = 'all' | 'golden' | 'death';

// ── Helper components ─────────────────────────────────────────────────────────

function Skeleton({ className }: { className: string }) {
  return (
    <div className={`bg-border/60 rounded animate-pulse ${className}`} />
  );
}

function CrossBadge({ cross }: { cross: 'golden' | 'death' }) {
  return cross === 'golden' ? (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold border bg-buy/12 text-buy border-buy/25">
      ⚡ Golden
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold border bg-sell/12 text-sell border-sell/25">
      💀 Death
    </span>
  );
}

function RegimeBadge({ inGolden }: { inGolden: boolean }) {
  return inGolden ? (
    <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold border bg-buy/12 text-buy border-buy/25">
      In Golden Cross
    </span>
  ) : (
    <span className="text-muted text-[10px]">—</span>
  );
}

function ExchangeBadge({ exchange }: { exchange: string }) {
  return (
    <span className="text-[9px] font-bold px-1.5 py-0.5 rounded border text-muted border-border bg-surface">
      {exchange}
    </span>
  );
}

function FilterChip<T extends string | number>({
  value, active, onClick, label,
}: { value: T; active: boolean; onClick: (v: T) => void; label: string }) {
  return (
    <button
      onClick={() => onClick(value)}
      className={`px-3 py-1.5 rounded-full text-[11px] font-semibold border transition-colors
        ${active
          ? 'bg-accent/15 border-accent/40 text-accent'
          : 'bg-surface border-border text-muted hover:text-tx hover:border-border-hi'}`}
    >
      {label}
    </button>
  );
}

// ── Loading skeleton rows ─────────────────────────────────────────────────────

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 8 }).map((_, i) => (
        <tr key={i} className="border-b border-border/60">
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-20" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-36" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-12" /></td>
          <td className="px-4 py-4"><Skeleton className="h-5 w-20" /></td>
          <td className="px-4 py-4"><Skeleton className="h-5 w-16" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-16 ml-auto" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-16 ml-auto" /></td>
          <td className="px-4 py-4"><Skeleton className="h-3.5 w-16 ml-auto" /></td>
        </tr>
      ))}
    </>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SmeSignalsPage() {
  const [data,       setData]       = useState<SmeSignalsResponse | null>(null);
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState<string | null>(null);
  const [lookback,   setLookback]   = useState<Lookback>(5);
  const [direction,  setDirection]  = useState<Direction>('all');
  const [refreshing, setRefreshing] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const fetchSignals = useCallback(async (lb: Lookback, dir: Direction, silent = false) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    if (!silent) setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({ lookback: String(lb), direction: dir });
      const res = await fetch(`/api/sme-signals?${qs}`, { signal: ac.signal });
      const json = await res.json() as SmeSignalsResponse & { error?: string };
      if (!res.ok) {
        setError(json.error ?? `Error ${res.status}`);
        setData(null);
      } else {
        setData(json);
      }
    } catch (e) {
      if ((e as Error).name === 'AbortError') return;
      setError('Could not reach the backend. Is the server running?');
      setData(null);
    } finally {
      // Whoever owns the latest request clears loading — a silent poll that
      // aborted a non-silent fetch must clear it too, or the skeleton sticks.
      if (abortRef.current === ac) setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSignals(lookback, direction);
  }, [lookback, direction, fetchSignals]);

  // Track server-side refresh state; poll while a refresh runs, reload when done.
  useEffect(() => {
    if (data) setRefreshing(data.refreshing);
  }, [data]);

  useEffect(() => {
    if (!refreshing) return;
    const t = setInterval(() => fetchSignals(lookback, direction, true), 10000);
    return () => clearInterval(t);
  }, [refreshing, lookback, direction, fetchSignals]);

  const startRefresh = useCallback(async () => {
    try {
      const res = await fetch('/api/sme-signals/refresh', { method: 'POST' });
      if (res.status === 202 || res.status === 409) setRefreshing(true);
    } catch {
      setError('Could not reach the backend. Is the server running?');
    }
  }, []);

  const signals = data?.signals ?? [];

  // Derived stats
  const deathCount = signals.filter(s => s.cross === 'death').length;

  const lastRunLabel = data?.last_run
    ? new Date(data.last_run).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })
    : null;

  return (
    <main className="min-h-screen bg-bg text-tx">
      <div className="max-w-5xl mx-auto px-4 pt-8 pb-16">

        {/* Nav */}
        <div className="flex items-center gap-4 mb-8 pb-4 border-b border-border">
          <Link href="/" className="text-base font-black tracking-tight text-tx">
            Stock<span className="text-accent">Research</span> AI
          </Link>
          <span className="text-border-hi">|</span>
          <Link href="/market-picks" className="text-sm text-muted hover:text-tx transition-colors">
            Market Picks
          </Link>
          <span className="text-border-hi">|</span>
          <span className="text-sm font-semibold text-accent">SME Signals</span>
          <div className="ml-auto flex items-center gap-3">
            <button
              onClick={startRefresh}
              disabled={refreshing}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-accent/40 text-accent
                         hover:bg-accent/10 transition-colors disabled:opacity-40"
            >
              {refreshing ? 'Refreshing data…' : '⟳ Refresh Data'}
            </button>
            <button
              onClick={() => fetchSignals(lookback, direction)}
              disabled={loading}
              className="text-xs text-muted hover:text-tx transition-colors disabled:opacity-40"
            >
              {loading ? 'Loading…' : '↺ Reload'}
            </button>
          </div>
        </div>

        {/* Header */}
        <div className="mb-8 animate-fade-up">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full
                          bg-accent/10 border border-accent/20 text-accent text-xs font-semibold mb-4">
            NSE Emerge · BSE SME · Golden Cross Screener
          </div>
          <h1 className="text-4xl font-black tracking-tight mb-2">
            SME Golden <span className="text-accent">Cross</span> Signals
          </h1>
          <p className="text-muted text-sm max-w-xl leading-relaxed">
            SME-listed stocks (NSE Emerge + BSE SME) whose EMA 20 crossed their EMA 50 (golden/death cross) in the selected window.
            Data is computed by the <code className="text-accent/80 text-[11px] bg-accent/8 px-1.5 py-0.5 rounded">sme_ema_pipeline</code> batch job.
          </p>
        </div>

        {/* Stats strip */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
          {[
            { label: 'Stocks Monitored',    value: data?.total_monitored ?? '—',        color: 'text-tx',     sub: 'NSE Emerge + BSE SME' },
            { label: 'Crosses Found',       value: loading ? '—' : signals.length,      color: 'text-accent', sub: `last ${lookback} day${lookback > 1 ? 's' : ''}` },
            { label: 'In Golden Cross Now', value: data?.golden_now ?? '—',             color: 'text-buy',    sub: 'EMA20 above EMA50 today' },
            { label: 'Death Crosses',       value: loading ? '—' : deathCount,          color: 'text-sell',   sub: `last ${lookback} day${lookback > 1 ? 's' : ''}` },
          ].map(({ label, value, color, sub }) => (
            <div key={label} className="rounded-xl border border-border bg-card px-4 py-3">
              <div className={`text-2xl font-black font-mono tabular-nums ${color}`}>{value}</div>
              <div className="text-[11px] font-semibold text-tx mt-0.5">{label}</div>
              <div className="text-[10px] text-muted mt-0.5">{sub}</div>
            </div>
          ))}
        </div>

        {/* Filters */}
        <div className="flex flex-wrap items-center gap-4 mb-6">
          {/* Lookback */}
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold text-muted uppercase tracking-wider">Period</span>
            <div className="flex gap-1.5">
              {([1, 3, 5, 10] as const).map(v => (
                <FilterChip key={v} value={v} active={lookback === v} onClick={setLookback} label={`${v}d`} />
              ))}
            </div>
          </div>

          <div className="w-px h-5 bg-border" />

          {/* Direction */}
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold text-muted uppercase tracking-wider">Direction</span>
            <div className="flex gap-1.5">
              {(
                [
                  { value: 'all',    label: 'All'      },
                  { value: 'golden', label: '⚡ Golden' },
                  { value: 'death',  label: '💀 Death'  },
                ] as const
              ).map(({ value, label }) => (
                <FilterChip key={value} value={value} active={direction === value} onClick={setDirection} label={label} />
              ))}
            </div>
          </div>
        </div>

        {/* Last run label */}
        {lastRunLabel && (
          <div className="text-[10px] text-muted mb-4">
            Pipeline last run: <span className="text-tx/60">{lastRunLabel}</span>
            {' '}·{' '}
            <span className="text-muted/60">
              Run <code className="text-accent/60">python sme_ema_pipeline.py</code> to update
            </span>
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="px-5 py-4 rounded-xl bg-sell/10 border border-sell/30 text-sell text-sm mb-6">
            {error}
          </div>
        )}

        {/* Table */}
        {!error && (
          <div className="rounded-xl border border-border overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-surface sticky top-0 z-10">
                    {[
                      { label: 'Symbol',     cls: 'text-left'  },
                      { label: 'Company',    cls: 'text-left'  },
                      { label: 'Cross Date', cls: 'text-left'  },
                      { label: 'Cross',      cls: 'text-left'  },
                      { label: 'Regime',     cls: 'text-left'  },
                      { label: 'Close',      cls: 'text-right' },
                      { label: 'EMA 20',     cls: 'text-right' },
                      { label: 'EMA 50',     cls: 'text-right' },
                    ].map(({ label, cls }) => (
                      <th
                        key={label}
                        className={`px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider ${cls}`}
                      >
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {loading ? (
                    <SkeletonRows />
                  ) : signals.length === 0 ? (
                    <tr>
                      <td colSpan={8} className="px-4 py-16 text-center text-muted text-sm">
                        No crossovers found for the selected filters.
                        {!data?.last_run && (
                          <div className="mt-2 text-xs text-muted/60">
                            Make sure you&apos;ve run{' '}
                            <code className="text-accent/60">python sme_ema_pipeline.py</code> first.
                          </div>
                        )}
                      </td>
                    </tr>
                  ) : (
                    signals.map((s, i) => (
                      <tr
                        key={`${s.symbol}-${s.trade_date}-${i}`}
                        className="border-b border-border/60 hover:bg-surface/60 transition-colors"
                      >
                        {/* Symbol */}
                        <td className="px-4 py-4">
                          <div className="flex items-center gap-1.5">
                            {s.exchange === 'NSE' ? (
                              <Link
                                href={`/?symbol=${s.symbol}`}
                                className="font-semibold text-tx hover:text-accent transition-colors text-sm"
                              >
                                {s.symbol}
                              </Link>
                            ) : (
                              <span className="font-semibold text-tx text-sm">{s.symbol}</span>
                            )}
                            <ExchangeBadge exchange={s.exchange} />
                          </div>
                        </td>

                        {/* Company */}
                        <td className="px-4 py-4 max-w-[200px]">
                          <span className="text-muted text-xs truncate block" title={s.name ?? ''}>
                            {s.name ?? '—'}
                          </span>
                        </td>

                        {/* Cross Date */}
                        <td className="px-4 py-4">
                          <span className="text-xs font-mono text-muted/80 tabular-nums">
                            {s.trade_date}
                          </span>
                        </td>

                        {/* Cross */}
                        <td className="px-4 py-4">
                          <CrossBadge cross={s.cross} />
                        </td>

                        {/* Regime */}
                        <td className="px-4 py-4">
                          <RegimeBadge inGolden={s.in_golden_cross} />
                        </td>

                        {/* Close price */}
                        <td className="px-4 py-4 text-right">
                          <span className="font-mono tabular-nums text-xs text-tx">
                            {s.close_price != null ? `₹${s.close_price.toFixed(2)}` : '—'}
                          </span>
                        </td>

                        {/* EMA 20 */}
                        <td className="px-4 py-4 text-right">
                          <span className="font-mono tabular-nums text-xs text-muted">
                            {s.ema20 != null ? s.ema20.toFixed(2) : '—'}
                          </span>
                        </td>

                        {/* EMA 50 */}
                        <td className="px-4 py-4 text-right">
                          <span className="font-mono tabular-nums text-xs text-muted">
                            {s.ema50 != null ? s.ema50.toFixed(2) : '—'}
                          </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Footer hint */}
        {!loading && !error && signals.length > 0 && (
          <p className="text-[10px] text-muted/50 mt-3">
            Click an NSE symbol to run full analysis. BSE SME symbols are scrip codes and can&apos;t be analysed directly.
          </p>
        )}

      </div>
    </main>
  );
}
