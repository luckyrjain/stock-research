'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useWatchlist } from '@/lib/watchlist';
import SiteNav from '@/components/site-nav';

interface LivePrice {
  // GET /api/prices returns an entry for every requested symbol, but it's
  // `{}` (not omitted, not null) when the price lookup itself failed (e.g.
  // yfinance had nothing for either the .NS or .BO suffix) — so `prices[sym]`
  // being truthy does NOT imply these fields are actually present.
  price?: number;
  change_pct?: number;
}

function Skeleton({ className }: { className: string }) {
  return <div className={`bg-border/60 rounded animate-pulse ${className}`} />;
}

export default function WatchlistPage() {
  const { items, loading, remove } = useWatchlist();
  const [prices, setPrices] = useState<Record<string, LivePrice>>({});
  const [pricesLoading, setPricesLoading] = useState(true);

  useEffect(() => {
    if (items.length === 0) { setPricesLoading(false); return; }
    const symbols = items.map(i => i.symbol).join(',');

    const fetchPrices = async () => {
      try {
        const res = await fetch(`/api/prices?symbols=${encodeURIComponent(symbols)}`);
        if (!res.ok) return;
        const data = await res.json() as { prices: Record<string, LivePrice> };
        setPrices(data.prices);
      } catch {
        // silently ignore — stale/missing price is fine, the row just shows "—"
      } finally {
        setPricesLoading(false);
      }
    };

    fetchPrices();
    const id = setInterval(fetchPrices, 30_000);
    return () => clearInterval(id);
  }, [items.map(i => i.symbol).join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  const sorted = [...items].sort(
    (a, b) => new Date(b.addedAt).getTime() - new Date(a.addedAt).getTime(),
  );

  return (
    <main className="min-h-screen bg-bg text-tx">
      <div className="max-w-5xl mx-auto px-4 pt-8 pb-16">

        <SiteNav active="watchlist" />

        <div className="mb-6">
          <h1 className="text-xl font-black tracking-tight text-tx mb-1.5">Watchlist</h1>
          <p className="text-muted text-sm max-w-xl leading-relaxed">
            Stocks you&apos;ve starred from stock analysis, Market Picks, or SME Signals, all in one
            place. Tied to this browser only — it won&apos;t follow you to another device yet.
          </p>
        </div>

        {loading || (pricesLoading && items.length > 0) ? (
          <div className="rounded-xl border border-border overflow-hidden">
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
        ) : sorted.length === 0 ? (
          <div className="rounded-xl border border-border bg-card px-6 py-16 text-center">
            <p className="text-sm text-muted mb-4">
              Nothing here yet. Tap the ☆ next to any stock in analysis, Market Picks, or SME
              Signals to add it.
            </p>
            <div className="flex items-center justify-center gap-3 flex-wrap">
              <Link href="/" className="text-xs font-semibold text-accent hover:underline">
                Analyse a stock →
              </Link>
              <Link href="/market-picks" className="text-xs font-semibold text-accent hover:underline">
                Browse Market Picks →
              </Link>
              <Link href="/sme-signals" className="text-xs font-semibold text-accent hover:underline">
                Browse SME Signals →
              </Link>
            </div>
          </div>
        ) : (
          <div className="rounded-xl border border-border overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-surface sticky top-0 z-10">
                    {[
                      { label: 'Symbol', cls: 'text-left' },
                      { label: 'Company', cls: 'text-left' },
                      { label: 'Exchange', cls: 'text-left' },
                      { label: 'Price', cls: 'text-right' },
                      { label: 'Change', cls: 'text-right' },
                      { label: '', cls: 'text-right' },
                    ].map(({ label, cls }) => (
                      <th key={label} className={`px-4 py-3 text-[10px] font-bold text-muted uppercase tracking-wider ${cls}`}>
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sorted.map(item => {
                    const live = prices[item.symbol];
                    return (
                      <tr key={item.symbol} className="border-b border-border/60 hover:bg-surface/60 transition-colors">
                        <td className="px-4 py-4">
                          <Link href={`/?symbol=${item.symbol}`} className="font-semibold text-tx hover:text-accent transition-colors text-sm">
                            {item.symbol}
                          </Link>
                        </td>
                        <td className="px-4 py-4 max-w-[220px]">
                          <span className="text-muted text-xs truncate block" title={item.company}>
                            {item.company}
                          </span>
                        </td>
                        <td className="px-4 py-4">
                          <span className="text-[9px] font-bold px-1.5 py-0.5 rounded border text-muted border-border bg-surface">
                            {item.exchange}
                          </span>
                        </td>
                        <td className="px-4 py-4 text-right">
                          <span className="font-mono tabular-nums text-xs text-tx">
                            {live?.price != null ? `₹${live.price.toFixed(2)}` : '—'}
                          </span>
                        </td>
                        <td className="px-4 py-4 text-right">
                          {live?.change_pct != null ? (
                            <span className={`font-mono tabular-nums text-xs font-semibold ${live.change_pct >= 0 ? 'text-buy' : 'text-sell'}`}>
                              {live.change_pct >= 0 ? '↑' : '↓'} {Math.abs(live.change_pct).toFixed(1)}%
                            </span>
                          ) : (
                            <span className="text-muted text-xs">—</span>
                          )}
                        </td>
                        <td className="px-4 py-4 text-right">
                          <button
                            onClick={() => remove(item.symbol)}
                            aria-label={`Remove ${item.symbol} from watchlist`}
                            className="text-xs text-muted hover:text-sell transition-colors"
                          >
                            Remove
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
