'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { usePositions, type Position } from '@/lib/positions';
import PageShell from '@/components/page-shell';
import { Skeleton } from '@/components/data-table-ui';
import { fmtPrice, fmtChangePct as fmtPct } from '@/lib/format';

interface LivePrice {
  price: number;
  change_pct: number | null;
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
  // shares is user-entered (see lib/positions.ts) — invested/currentValue are
  // only ever computed when the user has actually filled it in for this
  // position, never assumed to be 1.
  shares: number | null;
  invested: number | null;
  currentValue: number | null;
}

function fmtInr(n: number): string {
  if (Math.abs(n) >= 1_00_00_000) return `₹${(n / 1_00_00_000).toFixed(2)} Cr`;
  if (Math.abs(n) >= 1_00_000) return `₹${(n / 1_00_000).toFixed(2)} L`;
  return `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

/** Aggregate view over every "I bought this" position (frontend/lib/positions.ts,
 * account-linked positions table now, same ownership shape as the watchlist —
 * see PositionsStrip for the per-pick strip this generalizes). The price
 * polling itself is still purely client-side: reuses the same GET /api/prices
 * poll the Market Picks page already uses for live LTP, no new backend work
 * for that part.
 *
 * Positions carry an optional, user-entered share count (filled in via the
 * "Shares" column below, not asked for at "I bought this" click-time — that's
 * meant to stay a frictionless one-click action). A capital-weighted ₹
 * invested/current/P&L is only ever computed from positions where the user
 * has actually filled it in — never assumed to be "1 share each," which
 * would be inventing a number this app doesn't have. The equal-weighted
 * stats (win rate, average P&L%, best/worst) still cover every priced
 * position regardless of share count, since those don't need one.
 */
export default function PortfolioPage() {
  const { positions, loading, removePosition, updateShares } = usePositions();
  const [prices, setPrices] = useState<Record<string, LivePrice>>({});
  const [sortDesc, setSortDesc] = useState(true);
  // Stale/degraded (state 5, design.md §17): a poll that's been failing
  // must not render identically to one that's fresh.
  const [pricesUpdatedAt, setPricesUpdatedAt] = useState<Date | null>(null);
  const [pricesStale, setPricesStale] = useState(false);

  const symbolsKey = positions.map(p => p.symbol).join(',');

  useEffect(() => {
    if (positions.length === 0) return;
    let cancelled = false;

    const fetchPrices = async () => {
      try {
        const res = await fetch(`/api/prices?symbols=${encodeURIComponent(symbolsKey)}`);
        if (!res.ok) { if (!cancelled) setPricesStale(true); return; }
        const data = await res.json() as { prices: Record<string, LivePrice> };
        if (!cancelled) {
          setPrices(data.prices);
          setPricesUpdatedAt(new Date());
          setPricesStale(false);
        }
      } catch {
        // silently ignore in state — same convention as PositionsStrip —
        // but still mark the on-screen prices stale rather than leaving
        // them looking fresh indefinitely.
        if (!cancelled) setPricesStale(true);
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
      // Legacy positions created before shares tracking existed have no
      // `shares` key at all in their stored JSON (undefined, not null) —
      // normalize both to null here rather than trusting the stored shape.
      const shares = position.shares ?? null;
      return {
        position,
        current,
        pnl,
        atTarget: position.target_price != null && current != null && current >= position.target_price,
        atStop: position.stop_loss != null && current != null && current <= position.stop_loss,
        shares,
        invested: shares != null && position.entry_price != null ? shares * position.entry_price : null,
        currentValue: shares != null && current != null ? shares * current : null,
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

  // Capital-weighted view — only over rows where the user has actually
  // entered a share count (see the Row/updateShares plumbing above). A
  // position with no share count contributes nothing here, not a guessed
  // "1 share" fallback. Knowing how much capital went in (`invested`) only
  // needs `shares` + `entry_price` — it must NOT also require a live quote
  // to be available right now (a documented real failure mode of
  // GET /api/prices), so this is scoped to "has a share count," not "has a
  // share count AND is currently priced." A position that has a share
  // count but a momentarily-unavailable live price still contributes its
  // known invested amount here and still counts toward the "N of M
  // positions have a share count" label below.
  const withShares = rows.filter(r => r.shares != null);
  const totalInvested = withShares.reduce((sum, r) => sum + (r.invested ?? 0), 0);

  // Current value and P&L, unlike the invested total above, genuinely
  // can't be known without a live price — scoped to the (possibly smaller)
  // subset that's both share-tracked AND currently priced, using that
  // subset's own invested sum for the P&L subtraction rather than the
  // broader totalInvested above. Mixing populations there would silently
  // treat an unpriced position's current value as ₹0, understating the
  // portfolio's real value and overstating loss for no real reason.
  const pricedWithShares = withShares.filter(r => r.currentValue != null);
  const totalCurrentValue = pricedWithShares.reduce((sum, r) => sum + (r.currentValue ?? 0), 0);
  const pricedInvested = pricedWithShares.reduce((sum, r) => sum + (r.invested ?? 0), 0);
  const totalPnl = totalCurrentValue - pricedInvested;
  const totalPnlPct = pricedInvested > 0 ? (totalPnl / pricedInvested) * 100 : null;

  return (
    <PageShell active="portfolio" wrap maxWidth="max-w-5xl">

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
            An equal-weighted return summary across every &quot;I bought this&quot; position, plus a real
            ₹ invested/current value for whichever positions you&apos;ve added a share count to below
            (never assumed — a position with no share count just isn&apos;t counted in that total).
            Tied to this browser until you sign in, then it follows your account instead.
          </p>
        </div>

        {loading ? (
          <div className="rounded-xl border border-border overflow-hidden" aria-busy="true">
            <div className="divide-y divide-border/60">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="px-4 py-4 flex items-center gap-4">
                  <Skeleton className="h-4 w-16" />
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-4 w-16 ml-auto" />
                </div>
              ))}
            </div>
          </div>
        ) : positions.length === 0 ? (
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
            {pricesUpdatedAt && (
              <div className="flex justify-end mb-2">
                {pricesStale ? (
                  <span className="flex items-center gap-1 text-[10px] text-hold/80" title="The live-price refresh has been failing — prices below may be outdated">
                    <span className="w-1.5 h-1.5 rounded-full bg-hold inline-block" />
                    Prices may be outdated · last updated {pricesUpdatedAt.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true })}
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-[10px] text-buy/70">
                    <span className="w-1.5 h-1.5 rounded-full bg-buy animate-pulse inline-block" />
                    LTP {pricesUpdatedAt.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true })}
                  </span>
                )}
              </div>
            )}
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
                  <span className="text-muted/60 mx-0.5">/</span>
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

            {/* Capital-weighted value — only ever computed from positions the
                user has actually added a share count to (see the Shares
                column in the table below). Absent entirely rather than
                showing a zeroed-out ₹0 when nobody has filled one in yet. */}
            {withShares.length > 0 && (
              <div className="mb-6">
                <div className="flex items-center gap-2 mb-2.5">
                  <p className="text-[10px] font-bold uppercase tracking-wider text-muted">
                    Capital-Weighted Value
                  </p>
                  <span className="text-[10px] text-muted/60">
                    {withShares.length} of {positions.length} position{positions.length === 1 ? '' : 's'} have a share count
                  </span>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                  <div className="rounded-xl border border-border bg-card px-4 py-3">
                    <div className="text-[10px] font-bold uppercase tracking-wider text-muted mb-1">Invested</div>
                    <div className="text-xl font-black text-tx font-mono">{fmtInr(totalInvested)}</div>
                  </div>
                  {/* Current Value/P&L genuinely can't be known without at
                      least one currently-priced position (pricedWithShares)
                      -- unlike Invested above, which only needs share count
                      + entry price. Showing a numeric ₹0/+₹0 here when
                      nothing is priced yet (e.g. the GET /api/prices poll
                      hasn't resolved, or failed outright) would read as "your
                      portfolio is worth zero and flat" rather than "we don't
                      have prices yet" -- the same "absent, not a guessed
                      zero" principle the block's own comment above already
                      states for the Invested case. */}
                  <div className="rounded-xl border border-border bg-card px-4 py-3">
                    <div className="text-[10px] font-bold uppercase tracking-wider text-muted mb-1">Current Value</div>
                    <div className="text-xl font-black text-tx font-mono">
                      {pricedWithShares.length > 0 ? fmtInr(totalCurrentValue) : '—'}
                    </div>
                  </div>
                  <div className="rounded-xl border border-border bg-card px-4 py-3">
                    <div className="text-[10px] font-bold uppercase tracking-wider text-muted mb-1">P&amp;L</div>
                    {pricedWithShares.length > 0 ? (
                      <>
                        <div className={`text-xl font-black font-mono ${pnlColor(totalPnl)}`}>
                          {totalPnl >= 0 ? '+' : ''}{fmtInr(totalPnl)}
                        </div>
                        <div className={`text-[10px] ${pnlColor(totalPnlPct)}`}>{fmtPct(totalPnlPct)}</div>
                      </>
                    ) : (
                      <div className="text-xl font-black text-muted font-mono">—</div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* Table */}
            <div className="rounded-xl border border-border overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-surface">
                      <th className="text-left px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider">Symbol</th>
                      <th className="text-right px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider">Shares</th>
                      <th className="text-right px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider">Entry</th>
                      <th className="text-right px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider">Current</th>
                      <th className="text-right px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider">Value</th>
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
                        <td className="px-4 py-4">
                          <Link href={`/?symbol=${encodeURIComponent(row.position.symbol)}`} className="font-semibold text-tx hover:text-accent transition-colors">
                            {row.position.symbol}
                          </Link>
                          <div className="text-[10px] text-muted/70 truncate max-w-[10rem]">{row.position.company}</div>
                        </td>
                        <td className="px-4 py-4 text-right">
                          <input
                            type="number"
                            min={0}
                            step="any"
                            value={row.shares ?? ''}
                            onChange={e => {
                              const raw = e.target.value.trim();
                              if (raw === '') { updateShares(row.position.symbol, null); return; }
                              const parsed = Number(raw);
                              // min={0} on the input only affects the spinner/validity state, not
                              // typed input — a negative value would otherwise silently flow into
                              // invested/currentValue and distort the capital-weighted totals.
                              if (!Number.isNaN(parsed) && parsed >= 0) updateShares(row.position.symbol, parsed);
                            }}
                            placeholder="—"
                            aria-label={`Shares held of ${row.position.symbol}`}
                            className="w-16 px-1.5 py-1 rounded-md border border-border bg-surface text-tx text-xs text-right font-mono"
                          />
                        </td>
                        <td className="px-4 py-4 text-right font-mono">{fmtPrice(row.position.entry_price)}</td>
                        <td className="px-4 py-4 text-right font-mono">{fmtPrice(row.current)}</td>
                        <td className="px-4 py-4 text-right font-mono text-muted">
                          {row.currentValue != null ? fmtInr(row.currentValue) : '—'}
                        </td>
                        <td className={`px-4 py-4 text-right font-mono font-semibold ${pnlColor(row.pnl)}`}>{fmtPct(row.pnl)}</td>
                        <td className="px-4 py-4 text-right font-mono text-muted">{daysHeld(row.position.bought_at)}d</td>
                        <td className="px-4 py-4">
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
                        <td className="px-4 py-4">
                          <button
                            type="button"
                            onClick={() => removePosition(row.position.symbol)}
                            aria-label={`Remove ${row.position.symbol} from your positions`}
                            title="Remove from your positions"
                            className="text-muted/60 hover:text-sell transition-colors text-sm leading-none"
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
    </PageShell>
  );
}
