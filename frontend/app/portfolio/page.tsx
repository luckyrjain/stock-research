'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { usePositions, type Position } from '@/lib/positions';
import SiteNav from '@/components/site-nav';

interface LivePrice {
  price: number;
  change_pct: number;
}

function fmtPrice(n: number | null): string {
  if (n == null) return '—';
  return `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
}

function fmtPct(n: number | null): string {
  if (n == null) return '—';
  return `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`;
}

function pnlColor(n: number | null): string {
  if (n == null) return 'text-muted';
  return n >= 0 ? 'text-buy' : 'text-sell';
}

function pnlPct(entry: number | null, current: number | null): number | null {
  if (entry == null || current == null || entry <= 0) return null;
  return ((current - entry) / entry) * 100;
}

function daysHeld(boughtAt: string): number {
  const ms = Date.now() - new Date(boughtAt).getTime();
  return Math.max(0, Math.floor(ms / (1000 * 60 * 60 * 24)));
}

interface Row {
  position: Position;
  current: number | null;
  pnl: number | null;
  atTarget: boolean;
  atStop: boolean;
}

/** Aggregate view over every "I bought this" position (frontend/lib/positions.ts,
 * localStorage-only, no backend of its own — see PositionsStrip for the per-pick
 * strip this generalizes). Purely client-side: reuses the same GET /api/prices
 * poll the Market Picks page already uses for live LTP, no new backend work.
 *
 * Positions carry no share-count/quantity field (only entry/target/stop per
 * symbol), so a real capital-weighted portfolio value (₹ invested, ₹ current)
 * isn't data this page actually has — computing one would mean silently
 * assuming "1 share each," which is worse than not showing a number at all.
 * The aggregate stats here are therefore equal-weighted across positions
 * (win rate, average P&L%, best/worst performer) — a portfolio *return*
 * summary, not a portfolio *value* summary — and labeled as such.
 */
export default function PortfolioPage() {
  const { positions, removePosition } = usePositions();
  const [prices, setPrices] = useState<Record<string, LivePrice>>({});
  const [sortDesc, setSortDesc] = useState(true);

  const symbolsKey = positions.map(p => p.symbol).join(',');

  useEffect(() => {
    if (positions.length === 0) return;
    let cancelled = false;

    const fetchPrices = async () => {
      try {
        const res = await fetch(`/api/prices?symbols=${encodeURIComponent(symbolsKey)}`);
        if (!res.ok) return;
        const data = await res.json() as { prices: Record<string, LivePrice> };
        if (!cancelled) setPrices(data.prices);
      } catch {
        // silently ignore — stale/missing price is fine, same convention as PositionsStrip
      }
    };

    fetchPrices();
    const id = setInterval(fetchPrices, 30_000);
    return () => { cancelled = true; clearInterval(id); };
  }, [symbolsKey, positions.length]);

  const rows: Row[] = useMemo(() => {
    return positions.map(position => {
      const live = prices[position.symbol];
      const current = live?.price ?? null;
      const pnl = pnlPct(position.entry_price, current);
      return {
        position,
        current,
        pnl,
        atTarget: position.target_price != null && current != null && current >= position.target_price,
        atStop: position.stop_loss != null && current != null && current <= position.stop_loss,
      };
    });
  }, [positions, prices]);

  const sortedRows = useMemo(() => {
    return [...rows].sort((a, b) => {
      if (a.pnl == null && b.pnl == null) return 0;
      if (a.pnl == null) return 1;
      if (b.pnl == null) return -1;
      return sortDesc ? b.pnl - a.pnl : a.pnl - b.pnl;
    });
  }, [rows, sortDesc]);

  const priced = rows.filter(r => r.pnl != null);
  const winners = priced.filter(r => (r.pnl ?? 0) > 0);
  const losers = priced.filter(r => (r.pnl ?? 0) < 0);
  const flat = priced.length - winners.length - losers.length;
  const winRate = priced.length > 0 ? (winners.length / priced.length) * 100 : null;
  const avgPnl = priced.length > 0
    ? priced.reduce((sum, r) => sum + (r.pnl ?? 0), 0) / priced.length
    : null;
  const atTargetCount = rows.filter(r => r.atTarget).length;
  const atStopCount = rows.filter(r => r.atStop).length;
  const best = priced.length > 0 ? priced.reduce((a, b) => ((a.pnl ?? -Infinity) >= (b.pnl ?? -Infinity) ? a : b)) : null;
  const worst = priced.length > 0 ? priced.reduce((a, b) => ((a.pnl ?? Infinity) <= (b.pnl ?? Infinity) ? a : b)) : null;

  return (
    <main className="min-h-screen bg-bg text-tx">
      <div className="max-w-4xl mx-auto px-4 pt-8 pb-16">

        <SiteNav active="portfolio" wrap />

        {/* Header */}
        <div className="mb-8 animate-fade-up">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full
                          bg-accent/10 border border-accent/20 text-accent text-xs font-semibold mb-4">
            Your Tracked Positions
          </div>
          <h1 className="text-4xl font-black tracking-tight mb-2">
            Portfolio <span className="text-accent">Summary</span>
          </h1>
          <p className="text-muted text-sm max-w-2xl leading-relaxed">
            An equal-weighted return summary across every &quot;I bought this&quot; position — not a
            capital-weighted portfolio value, since no share count is tracked per position.
            Purely local to this browser; nothing here is sent to a server.
          </p>
        </div>

        {positions.length === 0 ? (
          <div className="rounded-xl border border-border bg-card px-6 py-10 text-center">
            <p className="text-muted text-sm mb-3">
              You haven&apos;t marked any picks as bought yet.
            </p>
            <Link href="/market-picks" className="text-xs font-semibold text-accent hover:underline">
              Browse Market Picks →
            </Link>
          </div>
        ) : (
          <>
            {/* Aggregate stats */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-8">
              <div className="rounded-xl border border-border bg-card px-4 py-3">
                <div className="text-[10px] font-bold uppercase tracking-wider text-muted mb-1">Positions</div>
                <div className="text-xl font-black text-tx">{positions.length}</div>
              </div>
              <div className="rounded-xl border border-border bg-card px-4 py-3">
                <div className="text-[10px] font-bold uppercase tracking-wider text-muted mb-1">Win Rate</div>
                <div className={`text-xl font-black ${winRate == null ? 'text-muted' : winRate >= 50 ? 'text-buy' : 'text-sell'}`}>
                  {winRate != null ? `${winRate.toFixed(0)}%` : '—'}
                </div>
                <div className="text-[10px] text-muted/70">
                  {winners.length}W / {losers.length}L{flat > 0 ? ` / ${flat} flat` : ''}
                </div>
              </div>
              <div className="rounded-xl border border-border bg-card px-4 py-3">
                <div className="text-[10px] font-bold uppercase tracking-wider text-muted mb-1">Avg P&amp;L</div>
                <div className={`text-xl font-black ${pnlColor(avgPnl)}`}>{fmtPct(avgPnl)}</div>
                <div className="text-[10px] text-muted/70">equal-weighted</div>
              </div>
              <div className="rounded-xl border border-border bg-card px-4 py-3">
                <div className="text-[10px] font-bold uppercase tracking-wider text-muted mb-1">At Target / Stop</div>
                <div className="text-xl font-black text-tx">
                  <span className="text-buy">{atTargetCount}</span>
                  <span className="text-muted/40 mx-0.5">/</span>
                  <span className="text-sell">{atStopCount}</span>
                </div>
              </div>
            </div>

            {(best || worst) && (
              <div className="flex flex-wrap gap-3 mb-6 text-xs">
                {best && best.pnl != null && (
                  <span className="text-muted">
                    Best: <span className="font-semibold text-tx">{best.position.symbol}</span>{' '}
                    <span className={pnlColor(best.pnl)}>{fmtPct(best.pnl)}</span>
                  </span>
                )}
                {worst && worst.pnl != null && (
                  <span className="text-muted">
                    Worst: <span className="font-semibold text-tx">{worst.position.symbol}</span>{' '}
                    <span className={pnlColor(worst.pnl)}>{fmtPct(worst.pnl)}</span>
                  </span>
                )}
              </div>
            )}

            {/* Table */}
            <div className="rounded-xl border border-border bg-card overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-surface/60">
                      <th className="text-left px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider">Symbol</th>
                      <th className="text-right px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider">Entry</th>
                      <th className="text-right px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider">Current</th>
                      <th
                        aria-sort={sortDesc ? 'descending' : 'ascending'}
                        className="text-right px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider"
                      >
                        <button
                          type="button"
                          onClick={() => setSortDesc(d => !d)}
                          className="inline-flex items-center gap-1 hover:text-tx transition-colors uppercase tracking-wider"
                        >
                          P&amp;L {sortDesc ? '↓' : '↑'}
                        </button>
                      </th>
                      <th className="text-right px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider">Days Held</th>
                      <th className="text-left px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider">Status</th>
                      <th className="w-10 px-4 py-3"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedRows.map(row => (
                      <tr key={row.position.symbol} className="border-b border-border/60 last:border-0 hover:bg-surface/40 transition-colors">
                        <td className="px-4 py-3">
                          <Link href={`/?symbol=${encodeURIComponent(row.position.symbol)}`} className="font-semibold text-tx hover:text-accent transition-colors">
                            {row.position.symbol}
                          </Link>
                          <div className="text-[10px] text-muted/70 truncate max-w-[10rem]">{row.position.company}</div>
                        </td>
                        <td className="px-4 py-3 text-right font-mono">{fmtPrice(row.position.entry_price)}</td>
                        <td className="px-4 py-3 text-right font-mono">{fmtPrice(row.current)}</td>
                        <td className={`px-4 py-3 text-right font-mono font-semibold ${pnlColor(row.pnl)}`}>{fmtPct(row.pnl)}</td>
                        <td className="px-4 py-3 text-right font-mono text-muted">{daysHeld(row.position.bought_at)}d</td>
                        <td className="px-4 py-3">
                          {row.atTarget ? (
                            <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold border bg-buy/12 text-buy border-buy/25">
                              At target
                            </span>
                          ) : row.atStop ? (
                            <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold border bg-sell/12 text-sell border-sell/25">
                              At stop
                            </span>
                          ) : (
                            <span className="text-muted text-[10px]">Holding</span>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <button
                            type="button"
                            onClick={() => removePosition(row.position.symbol)}
                            aria-label={`Remove ${row.position.symbol} from your positions`}
                            title="Remove from your positions"
                            className="text-muted/50 hover:text-sell transition-colors text-sm leading-none"
                          >
                            ×
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
      </div>
    </main>
  );
}
