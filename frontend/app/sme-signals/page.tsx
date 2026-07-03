'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import type { SmeSignal, SmeSignalsResponse } from '@/types';

// ── Filter types ──────────────────────────────────────────────────────────────

type Lookback  = 1 | 3 | 5 | 10;
type Direction = 'all' | 'bullish' | 'bearish';
type EmaFilter = 'all' | 'ema20' | 'ema50';

// ── Helper components ─────────────────────────────────────────────────────────

function Skeleton({ className }: { className: string }) {
  return (
    <div className={`bg-border/60 rounded animate-pulse ${className}`} />
  );
}

function DirectionBadge({ dir }: { dir: 'bullish' | 'bearish' | null }) {
  if (!dir) return <span className="text-muted text-[10px]">—</span>;
  return dir === 'bullish' ? (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold border bg-buy/12 text-buy border-buy/25">
      ↑ Bullish
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold border bg-sell/12 text-sell border-sell/25">
      ↓ Bearish
    </span>
  );
}

function CrossedBadge({ val }: { val: SmeSignal['crossed'] }) {
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold border bg-accent/10 text-accent border-accent/25">
      {val}
    </span>
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
  const [data,      setData]      = useState<SmeSignalsResponse | null>(null);
  const [loading,   setLoading]   = useState(true);
  const [error,     setError]     = useState<string | null>(null);
  const [lookback,  setLookback]  = useState<Lookback>(5);
  const [direction, setDirection] = useState<Direction>('all');
  const [ema,       setEma]       = useState<EmaFilter>('all');

  const fetchSignals = useCallback(async (lb: Lookback, dir: Direction, e: EmaFilter) => {
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({
        lookback:  String(lb),
        direction: dir,
        ema:       e,
      });
      const res = await fetch(`/api/sme-signals?${qs}`);
      const json = await res.json() as SmeSignalsResponse & { error?: string };
      if (!res.ok) {
        setError(json.error ?? `Error ${res.status}`);
        setData(null);
      } else {
        setData(json);
      }
    } catch {
      setError('Could not reach the backend. Is the server running?');
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSignals(lookback, direction, ema);
  }, [lookback, direction, ema, fetchSignals]);

  const signals = data?.signals ?? [];

  // Derived stats
  const bullishCount = signals.filter(s => s.cross_direction === 'bullish').length;
  const bearishCount = signals.filter(s => s.cross_direction === 'bearish').length;

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
          <button
            onClick={() => fetchSignals(lookback, direction, ema)}
            disabled={loading}
            className="ml-auto text-xs text-muted hover:text-tx transition-colors disabled:opacity-40"
          >
            {loading ? 'Loading…' : '↺ Refresh'}
          </button>
        </div>

        {/* Header */}
        <div className="mb-8 animate-fade-up">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full
                          bg-accent/10 border border-accent/20 text-accent text-xs font-semibold mb-4">
            NSE Emerge · BSE SME · EMA Crossover Screener
          </div>
          <h1 className="text-4xl font-black tracking-tight mb-2">
            SME EMA <span className="text-accent">Signals</span>
          </h1>
          <p className="text-muted text-sm max-w-xl leading-relaxed">
            SME-listed stocks (NSE Emerge + BSE SME) that crossed their EMA 20 or EMA 50 in the selected window.
            Data is computed by the <code className="text-accent/80 text-[11px] bg-accent/8 px-1.5 py-0.5 rounded">sme_ema_pipeline</code> batch job.
          </p>
        </div>

        {/* Stats strip */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
          {[
            {
              label: 'Stocks Monitored',
              value: data?.total_monitored ?? '—',
              color: 'text-tx',
              sub:   'NSE Emerge + BSE SME',
            },
            {
              label: 'Crossovers Found',
              value: loading ? '—' : signals.length,
              color: 'text-accent',
              sub:   `last ${lookback} day${lookback > 1 ? 's' : ''}`,
            },
            {
              label: 'Bullish',
              value: loading ? '—' : bullishCount,
              color: 'text-buy',
              sub:   'price crossed above EMA',
            },
            {
              label: 'Bearish',
              value: loading ? '—' : bearishCount,
              color: 'text-sell',
              sub:   'price crossed below EMA',
            },
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
              {([1, 3, 5, 10] as Lookback[]).map(v => (
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
                  { value: 'all',     label: 'All'     },
                  { value: 'bullish', label: '↑ Bullish' },
                  { value: 'bearish', label: '↓ Bearish' },
                ] as { value: Direction; label: string }[]
              ).map(({ value, label }) => (
                <FilterChip key={value} value={value} active={direction === value} onClick={setDirection} label={label} />
              ))}
            </div>
          </div>

          <div className="w-px h-5 bg-border" />

          {/* EMA */}
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold text-muted uppercase tracking-wider">EMA</span>
            <div className="flex gap-1.5">
              {(
                [
                  { value: 'all',   label: 'All'   },
                  { value: 'ema20', label: 'EMA 20' },
                  { value: 'ema50', label: 'EMA 50' },
                ] as { value: EmaFilter; label: string }[]
              ).map(({ value, label }) => (
                <FilterChip key={value} value={value} active={ema === value} onClick={setEma} label={label} />
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
                      { label: 'Symbol',    align: 'left'  },
                      { label: 'Company',   align: 'left'  },
                      { label: 'Date',      align: 'left'  },
                      { label: 'Crossed',   align: 'left'  },
                      { label: 'Direction', align: 'left'  },
                      { label: 'Close',     align: 'right' },
                      { label: 'EMA 20',    align: 'right' },
                      { label: 'EMA 50',    align: 'right' },
                    ].map(({ label, align }) => (
                      <th
                        key={label}
                        className={`px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider text-${align}`}
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
                            <Link
                              href={`/?symbol=${s.symbol}`}
                              className="font-semibold text-tx hover:text-accent transition-colors text-sm"
                            >
                              {s.symbol}
                            </Link>
                            <ExchangeBadge exchange={s.exchange} />
                          </div>
                        </td>

                        {/* Company */}
                        <td className="px-4 py-4 max-w-[200px]">
                          <span className="text-muted text-xs truncate block" title={s.name ?? ''}>
                            {s.name ?? '—'}
                          </span>
                        </td>

                        {/* Date */}
                        <td className="px-4 py-4">
                          <span className="text-xs font-mono text-muted/80 tabular-nums">
                            {s.trade_date}
                          </span>
                        </td>

                        {/* Crossed */}
                        <td className="px-4 py-4">
                          <CrossedBadge val={s.crossed} />
                        </td>

                        {/* Direction */}
                        <td className="px-4 py-4">
                          <DirectionBadge dir={s.cross_direction} />
                        </td>

                        {/* Close price */}
                        <td className="px-4 py-4 text-right">
                          <span className="font-mono tabular-nums text-xs text-tx">
                            {s.close_price != null ? `₹${s.close_price.toFixed(2)}` : '—'}
                          </span>
                        </td>

                        {/* EMA 20 */}
                        <td className="px-4 py-4 text-right">
                          <span className={`font-mono tabular-nums text-xs ${
                            s.crossed_ema20 ? 'text-accent' : 'text-muted'
                          }`}>
                            {s.ema20 != null ? s.ema20.toFixed(2) : '—'}
                          </span>
                        </td>

                        {/* EMA 50 */}
                        <td className="px-4 py-4 text-right">
                          <span className={`font-mono tabular-nums text-xs ${
                            s.crossed_ema50 ? 'text-accent' : 'text-muted'
                          }`}>
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
            Click a symbol to run full analysis. EMA values highlighted when that EMA was crossed.
          </p>
        )}

      </div>
    </main>
  );
}
