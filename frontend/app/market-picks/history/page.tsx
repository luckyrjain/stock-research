'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import type { MarketPicksHistoryResponse, MarketPickTrackRecord } from '@/types';

function RecBadge({ rec }: { rec: string | null }) {
  if (!rec) return <span className="text-muted text-xs">—</span>;
  const cls: Record<string, string> = {
    BUY:       'bg-buy/12 text-buy border-buy/25',
    WATCHLIST: 'bg-buy/8 text-buy/75 border-buy/15',
    HOLD:      'bg-hold/12 text-hold border-hold/25',
    SELL:      'bg-sell/12 text-sell border-sell/25',
  };
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold border ${cls[rec] ?? cls.HOLD}`}>
      {rec}
    </span>
  );
}

function Skeleton({ className }: { className: string }) {
  return <div className={`bg-border/60 rounded animate-pulse ${className}`} />;
}

export default function MarketPicksHistoryPage() {
  const [data,    setData]    = useState<MarketPicksHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch('/api/market-picks/history')
      .then(async res => {
        const json = await res.json().catch(() => null);
        if (!res.ok) throw new Error((json && json.error) || `Error ${res.status}`);
        return json as MarketPicksHistoryResponse;
      })
      .then(json => { if (!cancelled) setData(json); })
      .catch((e: Error) => { if (!cancelled) setError(e.message || 'Could not reach the backend.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const symbols: MarketPickTrackRecord[] = data?.symbols ?? [];
  const withPriceData = symbols.filter(s => s.change_pct != null);
  const avgChange = withPriceData.length > 0
    ? withPriceData.reduce((sum, s) => sum + (s.change_pct ?? 0), 0) / withPriceData.length
    : null;

  return (
    <main className="min-h-screen bg-bg text-tx">
      <div className="max-w-5xl mx-auto px-4 pt-8 pb-16">

        {/* Nav */}
        <div className="flex items-center gap-4 mb-8 pb-4 border-b border-border">
          <Link href="/" className="text-base font-black tracking-tight text-tx">
            Alpha<span className="text-accent">Pulse</span>
          </Link>
          <span className="text-border-hi">|</span>
          <Link href="/market-picks" className="text-sm text-muted hover:text-tx transition-colors">
            Market Picks
          </Link>
          <span className="text-border-hi">|</span>
          <Link href="/sme-signals" className="text-sm text-muted hover:text-tx transition-colors">
            SME Signals
          </Link>
          <span className="text-border-hi">|</span>
          <span className="text-sm font-semibold text-accent">Track Record</span>
        </div>

        <div className="mb-6">
          <h1 className="text-xl font-black tracking-tight text-tx mb-1.5">Pick Track Record</h1>
          <p className="text-muted text-sm max-w-xl leading-relaxed">
            How past Market Picks have moved since they were first surfaced, built from the daily
            snapshots in <code className="text-accent/80 text-[11px] bg-accent/8 px-1.5 py-0.5 rounded">output/_history/</code>.
            Price tracking only started with this snapshot format — older entries show &mdash; until enough new data accumulates.
          </p>
        </div>

        {/* Stats strip */}
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-6">
          <div className="rounded-xl border border-border bg-card px-4 py-3">
            <div className="text-2xl font-black font-mono tabular-nums text-tx">
              {loading ? <Skeleton className="h-6 w-10" /> : (data?.snapshot_count ?? 0)}
            </div>
            <div className="text-[11px] font-semibold text-tx mt-0.5">Daily Snapshots</div>
          </div>
          <div className="rounded-xl border border-border bg-card px-4 py-3">
            <div className="text-2xl font-black font-mono tabular-nums text-tx">
              {loading ? <Skeleton className="h-6 w-10" /> : symbols.length}
            </div>
            <div className="text-[11px] font-semibold text-tx mt-0.5">Stocks Tracked</div>
          </div>
          <div className="rounded-xl border border-border bg-card px-4 py-3">
            <div className={`text-2xl font-black font-mono tabular-nums ${
              avgChange == null ? 'text-tx' : avgChange >= 0 ? 'text-buy' : 'text-sell'
            }`}>
              {loading ? <Skeleton className="h-6 w-14" /> : avgChange == null ? '—' : `${avgChange >= 0 ? '+' : ''}${avgChange.toFixed(1)}%`}
            </div>
            <div className="text-[11px] font-semibold text-tx mt-0.5">Avg. Change Since Pick</div>
          </div>
        </div>

        {error && (
          <div className="px-5 py-4 rounded-xl bg-sell/10 border border-sell/30 text-sell text-sm mb-6">
            {error}
          </div>
        )}

        {!error && (
          <div className="rounded-xl border border-border overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-surface sticky top-0 z-10">
                    {[
                      { label: 'Symbol',       cls: 'text-left'  },
                      { label: 'First Seen',   cls: 'text-left'  },
                      { label: 'Times Picked', cls: 'text-right' },
                      { label: 'Rec (then → now)', cls: 'text-left' },
                      { label: 'Price Then',   cls: 'text-right' },
                      { label: 'Price Now',    cls: 'text-right' },
                      { label: 'Change',       cls: 'text-right' },
                    ].map(({ label, cls }) => (
                      <th key={label} className={`px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider ${cls}`}>
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {loading ? (
                    Array.from({ length: 6 }).map((_, i) => (
                      <tr key={i} className="border-b border-border/60">
                        <td className="px-4 py-4"><Skeleton className="h-3.5 w-16" /></td>
                        <td className="px-4 py-4"><Skeleton className="h-3.5 w-20" /></td>
                        <td className="px-4 py-4"><Skeleton className="h-3.5 w-8 ml-auto" /></td>
                        <td className="px-4 py-4"><Skeleton className="h-5 w-20" /></td>
                        <td className="px-4 py-4"><Skeleton className="h-3.5 w-14 ml-auto" /></td>
                        <td className="px-4 py-4"><Skeleton className="h-3.5 w-14 ml-auto" /></td>
                        <td className="px-4 py-4"><Skeleton className="h-3.5 w-12 ml-auto" /></td>
                      </tr>
                    ))
                  ) : symbols.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="px-4 py-16 text-center text-muted text-sm">
                        No track record yet — this fills in as daily Market Picks snapshots accumulate.
                      </td>
                    </tr>
                  ) : (
                    symbols.map(s => (
                      <tr key={s.symbol} className="border-b border-border/60 hover:bg-surface/60 transition-colors">
                        <td className="px-4 py-4">
                          <Link href={`/?symbol=${s.symbol}`} className="font-semibold text-tx hover:text-accent transition-colors text-sm">
                            {s.symbol}
                          </Link>
                        </td>
                        <td className="px-4 py-4">
                          <span className="text-xs font-mono text-muted/80 tabular-nums">{s.first_seen}</span>
                        </td>
                        <td className="px-4 py-4 text-right">
                          <span className="font-mono tabular-nums text-xs text-tx">{s.times_picked}</span>
                        </td>
                        <td className="px-4 py-4">
                          <div className="flex items-center gap-1.5">
                            <RecBadge rec={s.recommendation_then} />
                            <span className="text-muted/50 text-[10px]">→</span>
                            <RecBadge rec={s.recommendation_now} />
                          </div>
                        </td>
                        <td className="px-4 py-4 text-right">
                          <span className="font-mono tabular-nums text-xs text-muted">
                            {s.price_then != null ? `₹${s.price_then.toFixed(2)}` : '—'}
                          </span>
                        </td>
                        <td className="px-4 py-4 text-right">
                          <span className="font-mono tabular-nums text-xs text-tx">
                            {s.price_now != null ? `₹${s.price_now.toFixed(2)}` : '—'}
                          </span>
                        </td>
                        <td className="px-4 py-4 text-right">
                          {s.change_pct != null ? (
                            <span className={`font-mono tabular-nums text-xs font-semibold ${s.change_pct >= 0 ? 'text-buy' : 'text-sell'}`}>
                              {s.change_pct >= 0 ? '↑' : '↓'} {Math.abs(s.change_pct).toFixed(1)}%
                            </span>
                          ) : (
                            <span className="text-muted text-xs">—</span>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
