'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePositions } from '@/lib/positions';
import { fmtPrice } from '@/lib/format';

interface LivePrice {
  price: number;
  change_pct: number | null;
}

function pnlPct(entry: number | null, current: number | null): number | null {
  if (entry == null || current == null || entry <= 0) return null;
  return ((current - entry) / entry) * 100;
}

/** "I bought this" positions strip — reuses the same /api/prices poll the rest
 * of Market Picks already uses for live LTP, computing P&L against each
 * position's entry/target/stop-loss (captured at mark time, not re-fetched).
 * Renders nothing when the user hasn't marked any pick as bought. */
export default function PositionsStrip() {
  const { positions, removePosition } = usePositions();
  const [prices, setPrices] = useState<Record<string, LivePrice>>({});
  // Stale/degraded (state 5, design.md §17): a poll that's been failing
  // must not render identically to one that's fresh.
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
        if (!cancelled) { setPrices(data.prices); setPricesStale(false); }
      } catch {
        // silently ignore in state — same convention as the rest of Market
        // Picks' price polling — but still mark prices stale rather than
        // leaving them looking fresh indefinitely.
        if (!cancelled) setPricesStale(true);
      }
    };

    fetchPrices();
    const id = setInterval(fetchPrices, 30_000);
    return () => { cancelled = true; clearInterval(id); };
  }, [symbolsKey, positions.length]);

  if (positions.length === 0) return null;

  return (
    <div className="mb-8">
      <div className="flex items-center gap-2 mb-2.5">
        <h2 className="text-xs font-bold text-muted uppercase tracking-widest">Your Positions</h2>
        <span className="text-[10px] text-muted/60">{positions.length} tracked</span>
        {pricesStale && (
          <span className="text-[10px] text-hold/80" title="The live-price refresh has been failing — prices below may be outdated">
            · prices may be outdated
          </span>
        )}
        <Link href="/portfolio" className="ml-auto text-[10px] font-semibold text-accent hover:underline">
          View full portfolio →
        </Link>
      </div>
      <div className="flex gap-3 overflow-x-auto pb-1">
        {positions.map(pos => {
          const live = prices[pos.symbol];
          const current = live?.price ?? null;
          const pnl = pnlPct(pos.entry_price, current);
          const nearTarget = pos.target_price != null && current != null && current >= pos.target_price;
          const nearStop = pos.stop_loss != null && current != null && current <= pos.stop_loss;

          return (
            <div
              key={pos.symbol}
              className="shrink-0 w-56 rounded-xl border border-border bg-card px-3.5 py-3"
            >
              <div className="flex items-start justify-between gap-2 mb-2">
                <div className="min-w-0">
                  <div className="font-semibold text-tx text-sm truncate">{pos.symbol}</div>
                  <div className="text-[10px] text-muted/70 truncate">{pos.company}</div>
                </div>
                <button
                  type="button"
                  onClick={() => removePosition(pos.symbol)}
                  aria-label={`Remove ${pos.symbol} from your positions`}
                  title="Remove from your positions"
                  className="text-muted/60 hover:text-sell transition-colors text-sm leading-none shrink-0"
                >
                  ×
                </button>
              </div>

              <div className="flex items-baseline justify-between mb-1.5">
                <span className="font-mono tabular-nums text-sm font-black text-tx">{fmtPrice(current)}</span>
                {pnl != null && (
                  <span className={`font-mono tabular-nums text-xs font-bold ${pnl >= 0 ? 'text-buy' : 'text-sell'}`}>
                    {pnl >= 0 ? '+' : ''}{pnl.toFixed(1)}%
                  </span>
                )}
              </div>

              <div className="flex items-center justify-between text-[10px] text-muted/70 font-mono tabular-nums">
                <span>Entry {fmtPrice(pos.entry_price)}</span>
                <span>Target {fmtPrice(pos.target_price)}</span>
              </div>

              {(nearTarget || nearStop) && (
                <div className={`mt-2 text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded-full
                                  inline-block border
                                  ${nearTarget ? 'bg-buy/12 text-buy border-buy/25' : 'bg-sell/12 text-sell border-sell/25'}`}>
                  {nearTarget ? 'At target' : 'At stop-loss'}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
