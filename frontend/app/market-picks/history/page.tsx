'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import type { MarketPicksHistoryResponse, MarketPickTrackRecord, MarketPicksDailySnapshot } from '@/types';
import SiteNav from '@/components/site-nav';

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

function pctColor(v: number | null): string {
  if (v == null) return 'text-tx';
  return v >= 0 ? 'text-buy' : 'text-sell';
}

function fmtPct(v: number | null): string {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;
}

const TIER_ORDER = ['BUY', 'WATCHLIST', 'HOLD', 'SELL'];

export default function MarketPicksHistoryPage() {
  const [data,    setData]    = useState<MarketPicksHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);

  // A specific day's full pick list, browsed via the date picker below — a
  // separate fetch from the cross-date aggregation above, since the backend
  // returns a completely different shape for ?date=.
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [daily,        setDaily]        = useState<MarketPicksDailySnapshot | null>(null);
  const [dailyLoading, setDailyLoading] = useState(false);
  const [dailyError,   setDailyError]   = useState<string | null>(null);

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

  useEffect(() => {
    if (!selectedDate) { setDaily(null); setDailyError(null); return; }
    let cancelled = false;
    setDailyLoading(true);
    setDailyError(null);
    fetch(`/api/market-picks/history?date=${encodeURIComponent(selectedDate)}`)
      .then(async res => {
        const json = await res.json().catch(() => null);
        if (!res.ok) throw new Error((json && (json.detail || json.error)) || `Error ${res.status}`);
        return json as MarketPicksDailySnapshot;
      })
      .then(json => { if (!cancelled) setDaily(json); })
      .catch((e: Error) => { if (!cancelled) setDailyError(e.message || "Could not load that day's snapshot."); })
      .finally(() => { if (!cancelled) setDailyLoading(false); });
    return () => { cancelled = true; };
  }, [selectedDate]);

  const symbols: MarketPickTrackRecord[] = data?.symbols ?? [];
  const availableDates = data?.available_dates ?? [];
  const minDate = availableDates[0];
  const maxDate = availableDates[availableDates.length - 1];

  function stepDate(delta: number) {
    if (!selectedDate || availableDates.length === 0) return;
    const idx = availableDates.indexOf(selectedDate);
    const nextIdx = idx + delta;
    if (nextIdx >= 0 && nextIdx < availableDates.length) setSelectedDate(availableDates[nextIdx]);
  }
  const withPriceData = symbols.filter(s => s.change_pct != null);
  const avgChange = withPriceData.length > 0
    ? withPriceData.reduce((sum, s) => sum + (s.change_pct ?? 0), 0) / withPriceData.length
    : null;

  return (
    <main className="min-h-screen bg-bg text-tx">
      <div className="max-w-5xl mx-auto px-4 pt-8 pb-16">

        <SiteNav active="track-record" />

        <div className="mb-6">
          <h1 className="text-xl font-black tracking-tight text-tx mb-1.5">Pick Track Record</h1>
          <p className="text-muted text-sm max-w-xl leading-relaxed">
            How past Market Picks have moved since they were first surfaced, built from the daily
            snapshots in <code className="text-accent/80 text-[11px] bg-accent/8 px-1.5 py-0.5 rounded">output/_history/</code>.
            Price tracking only started with this snapshot format — older entries show &mdash; until enough new data accumulates.
          </p>
        </div>

        {/* Stats strip */}
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 mb-4">
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
          <div className="rounded-xl border border-border bg-card px-4 py-3">
            <div className="text-2xl font-black font-mono tabular-nums text-tx">
              {loading ? <Skeleton className="h-6 w-14" /> : data?.win_rate == null ? '—' : `${data.win_rate.toFixed(1)}%`}
            </div>
            <div className="text-[11px] font-semibold text-tx mt-0.5">Win Rate</div>
          </div>
          <div className="rounded-xl border border-border bg-card px-4 py-3">
            <div className={`text-2xl font-black font-mono tabular-nums ${loading ? 'text-tx' : pctColor(data?.avg_alpha_pct ?? null)}`}>
              {loading ? <Skeleton className="h-6 w-14" /> : fmtPct(data?.avg_alpha_pct ?? null)}
            </div>
            <div className="text-[11px] font-semibold text-tx mt-0.5">Avg. Alpha vs Nifty</div>
          </div>
        </div>

        {/* Per-tier breakdown */}
        {!loading && data && Object.keys(data.tier_stats).length > 0 && (
          <div className="flex flex-wrap gap-3 mb-6">
            {TIER_ORDER.filter(tier => data.tier_stats[tier]).map(tier => {
              const stat = data.tier_stats[tier];
              return (
                <div key={tier} className="rounded-xl border border-border bg-card px-4 py-2.5 flex items-center gap-3">
                  <RecBadge rec={tier} />
                  <span className="text-xs text-muted">{stat.count} pick{stat.count === 1 ? '' : 's'}</span>
                  <span className={`text-sm font-mono font-semibold ${pctColor(stat.avg_change_pct)}`}>
                    {fmtPct(stat.avg_change_pct)} avg
                  </span>
                  <span className="text-xs text-muted">·</span>
                  <span className="text-sm font-mono font-semibold text-tx">{stat.win_rate.toFixed(0)}% win rate</span>
                </div>
              );
            })}
          </div>
        )}

        {error && (
          <div className="px-5 py-4 rounded-xl bg-sell/10 border border-sell/30 text-sell text-sm mb-6">
            {error}
          </div>
        )}

        {/* Date picker — browse a specific day's full pick list, instead of
            the aggregated per-symbol roll-up below */}
        {!error && !loading && (
          <div className="flex flex-wrap items-center gap-2 mb-4">
            <label htmlFor="history-date" className="text-xs font-semibold text-muted mr-1">
              Browse a specific day
            </label>
            <input
              id="history-date"
              type="date"
              value={selectedDate ?? ''}
              min={minDate}
              max={maxDate}
              disabled={availableDates.length === 0}
              onChange={e => setSelectedDate(e.target.value || null)}
              className="bg-card border border-border rounded-xl px-3 py-2 text-xs text-tx
                         focus:outline-none focus:border-accent/40 transition-colors disabled:opacity-50"
            />
            <button
              type="button"
              onClick={() => stepDate(-1)}
              disabled={!selectedDate || availableDates.indexOf(selectedDate) <= 0}
              aria-label="Previous snapshot day"
              className="px-2.5 py-2 rounded-lg border border-border text-xs text-muted
                         hover:text-tx hover:border-border-hi transition-colors
                         disabled:opacity-30 disabled:pointer-events-none"
            >
              ← Prev
            </button>
            <button
              type="button"
              onClick={() => stepDate(1)}
              disabled={!selectedDate || availableDates.indexOf(selectedDate) >= availableDates.length - 1}
              aria-label="Next snapshot day"
              className="px-2.5 py-2 rounded-lg border border-border text-xs text-muted
                         hover:text-tx hover:border-border-hi transition-colors
                         disabled:opacity-30 disabled:pointer-events-none"
            >
              Next →
            </button>
            {selectedDate && (
              <button
                type="button"
                onClick={() => setSelectedDate(null)}
                className="px-2.5 py-2 rounded-lg text-xs text-muted hover:text-tx transition-colors"
              >
                ✕ Back to aggregated view
              </button>
            )}
          </div>
        )}

        {!error && selectedDate && (
          <div className="rounded-xl border border-border overflow-hidden mb-6">
            {dailyError ? (
              <div className="px-5 py-4 text-sell text-sm">{dailyError}</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-surface sticky top-0 z-10">
                      {[
                        { label: 'Symbol',         cls: 'text-left'  },
                        { label: 'Recommendation', cls: 'text-left'  },
                        { label: 'Confidence',     cls: 'text-right' },
                        { label: 'Signal',         cls: 'text-right' },
                        { label: 'Mentions',       cls: 'text-right' },
                        { label: 'Price',          cls: 'text-right' },
                      ].map(({ label, cls }) => (
                        <th key={label} className={`px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider ${cls}`}>
                          {label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {dailyLoading ? (
                      Array.from({ length: 6 }).map((_, i) => (
                        <tr key={i} className="border-b border-border/60">
                          <td className="px-4 py-4"><Skeleton className="h-3.5 w-16" /></td>
                          <td className="px-4 py-4"><Skeleton className="h-5 w-20" /></td>
                          <td className="px-4 py-4"><Skeleton className="h-3.5 w-10 ml-auto" /></td>
                          <td className="px-4 py-4"><Skeleton className="h-3.5 w-10 ml-auto" /></td>
                          <td className="px-4 py-4"><Skeleton className="h-3.5 w-8 ml-auto" /></td>
                          <td className="px-4 py-4"><Skeleton className="h-3.5 w-14 ml-auto" /></td>
                        </tr>
                      ))
                    ) : !daily || daily.picks.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="px-4 py-16 text-center text-muted text-sm">
                          No picks recorded in this snapshot.
                        </td>
                      </tr>
                    ) : (
                      daily.picks.map(p => (
                        <tr key={p.symbol} className="border-b border-border/60 hover:bg-surface/60 transition-colors">
                          <td className="px-4 py-4">
                            <Link href={`/?symbol=${encodeURIComponent(p.symbol)}`} className="font-semibold text-tx hover:text-accent transition-colors text-sm">
                              {p.symbol}
                            </Link>
                          </td>
                          <td className="px-4 py-4"><RecBadge rec={p.recommendation} /></td>
                          <td className="px-4 py-4 text-right">
                            <span className="font-mono tabular-nums text-xs text-tx">{p.confidence.toFixed(0)}</span>
                          </td>
                          <td className="px-4 py-4 text-right">
                            <span className={`font-mono tabular-nums text-xs font-semibold ${pctColor(p.effective_signal)}`}>
                              {p.effective_signal.toFixed(2)}
                            </span>
                          </td>
                          <td className="px-4 py-4 text-right">
                            <span className="font-mono tabular-nums text-xs text-muted">{p.mention_count}</span>
                          </td>
                          <td className="px-4 py-4 text-right">
                            <span className="font-mono tabular-nums text-xs text-tx">
                              {p.current_price != null ? `₹${p.current_price.toFixed(2)}` : '—'}
                            </span>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {!error && !selectedDate && (
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
                      { label: 'vs Nifty',     cls: 'text-right' },
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
                        <td className="px-4 py-4"><Skeleton className="h-3.5 w-12 ml-auto" /></td>
                      </tr>
                    ))
                  ) : symbols.length === 0 ? (
                    <tr>
                      <td colSpan={8} className="px-4 py-16 text-center text-muted text-sm">
                        No track record yet — this fills in as daily Market Picks snapshots accumulate.
                      </td>
                    </tr>
                  ) : (
                    symbols.map(s => (
                      <tr key={s.symbol} className="border-b border-border/60 hover:bg-surface/60 transition-colors">
                        <td className="px-4 py-4">
                          <Link href={`/?symbol=${encodeURIComponent(s.symbol)}`} className="font-semibold text-tx hover:text-accent transition-colors text-sm">
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
                        <td className="px-4 py-4 text-right">
                          <span
                            className={`font-mono tabular-nums text-xs font-semibold ${pctColor(s.alpha_pct)}`}
                            title={s.nifty_change_pct != null ? `Nifty over the same window: ${fmtPct(s.nifty_change_pct)}` : undefined}
                          >
                            {fmtPct(s.alpha_pct)}
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
      </div>
    </main>
  );
}
