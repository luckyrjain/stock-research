'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useWatchlist } from '@/lib/watchlist';
import SiteNav from '@/components/site-nav';
import { Skeleton } from '@/components/data-table-ui';
import type { WatchlistCalendarEntry } from '@/types';

interface LivePrice {
  // GET /api/prices returns an entry for every requested symbol, but it's
  // `{}` (not omitted, not null) when the price lookup itself failed (e.g.
  // yfinance had nothing for either the .NS or .BO suffix) — so `prices[sym]`
  // being truthy does NOT imply these fields are actually present.
  price?: number;
  // null when the price resolved but yfinance had no previous_close to
  // diff against — a real "change unknown", not a fabricated flat 0%.
  change_pct?: number | null;
}

// Pure read-aggregation over each watched symbol's already-cached filings
// (see GET /api/watchlist/calendar) — no new scrape, so a symbol that
// hasn't been analysed recently (or ever) just contributes nothing, not an
// error. Watchlist-wide, so this hook is local to this page, not shared
// with useWatchlist()'s module-level cache.
function useWatchlistCalendar(symbols: string[]): WatchlistCalendarEntry[] {
  const [entries, setEntries] = useState<WatchlistCalendarEntry[]>([]);
  const key = symbols.join(',');

  useEffect(() => {
    if (!key) { setEntries([]); return; }
    let cancelled = false;
    fetch(`/api/watchlist/calendar?symbols=${encodeURIComponent(key)}`)
      .then(res => (res.ok ? res.json() : null))
      .then((data: { entries: WatchlistCalendarEntry[] } | null) => {
        if (!cancelled) setEntries(data?.entries ?? []);
      })
      .catch(() => { if (!cancelled) setEntries([]); });
    return () => { cancelled = true; };
  }, [key]);

  return entries;
}

const REC_TONE: Record<string, string> = {
  BUY:  'text-buy border-buy/40 bg-buy/10',
  SELL: 'text-sell border-sell/40 bg-sell/10',
  HOLD: 'text-hold border-hold/40 bg-hold/10',
};

// The headline fix this section exists for: watchlist_alerts.py's daily
// digest already computes a same-day recommendation-change / price-move
// flag and emails it once a day — previously that was the ONLY way to learn
// about it. This badge surfaces the exact same data in-app instead of
// leaving it to arrive (or not) as an email that may not even be
// configured (no SMTP_HOST = the email silently never sends).
function AlertBadges({ entry }: { entry: WatchlistCalendarEntry }) {
  return (
    <>
      {entry.price_move && (
        <span
          className={`px-2 py-0.5 rounded-full border font-mono font-semibold ${
            entry.price_move.change_pct >= 0 ? 'text-buy border-buy/40 bg-buy/10' : 'text-sell border-sell/40 bg-sell/10'
          }`}
          title={`₹${entry.price_move.old_price} → ₹${entry.price_move.new_price} since the last stored verdict`}
        >
          {entry.price_move.change_pct >= 0 ? '+' : ''}{entry.price_move.change_pct}% since last check
        </span>
      )}
      {entry.recommendation_change && (
        <span className={`px-2 py-0.5 rounded-full border font-semibold ${REC_TONE[entry.recommendation_change.new_recommendation] ?? 'text-tx border-border bg-surface'}`}>
          {entry.recommendation_change.old_recommendation ?? '—'} → {entry.recommendation_change.new_recommendation}
        </span>
      )}
    </>
  );
}

function CalendarStrip({ entries }: { entries: WatchlistCalendarEntry[] }) {
  if (entries.length === 0) return null;
  return (
    <div className="mb-6 rounded-xl border border-border bg-card p-4">
      <p className="text-[11px] font-semibold text-muted tracking-[1px] uppercase mb-3">
        Upcoming &amp; notable — from your watchlist&apos;s cached filings and latest verdicts
      </p>
      <div className="space-y-2">
        {entries.map(e => (
          <div key={e.symbol} className="flex items-center gap-2 flex-wrap text-xs">
            <Link href={`/?symbol=${encodeURIComponent(e.symbol)}`} className="font-mono font-semibold text-tx hover:text-accent transition-colors shrink-0">
              {e.symbol}
            </Link>
            <AlertBadges entry={e} />
            {e.next_results_date && (
              <span className="px-2 py-0.5 rounded-full bg-surface border border-border text-tx">
                Next results: {e.next_results_date}
              </span>
            )}
            {e.rating_action && (
              <span
                className={`px-2 py-0.5 rounded-full border capitalize ${
                  e.rating_action.action === 'upgrade'
                    ? 'text-buy border-buy/40 bg-buy/10'
                    : e.rating_action.action === 'downgrade'
                    ? 'text-sell border-sell/40 bg-sell/10'
                    : 'text-hold border-hold/40 bg-hold/10'
                }`}
                title={e.rating_action.title ?? undefined}
              >
                {e.rating_action.agency} {e.rating_action.action}
                {e.rating_action.date ? ` · ${e.rating_action.date}` : ''}
              </span>
            )}
            {e.corporate_actions.slice(0, 2).map((ca, i) => (
              <span
                key={i}
                className="px-2 py-0.5 rounded-full bg-surface border border-border text-tx capitalize"
                title={ca.title ?? undefined}
              >
                {ca.type}{ca.date ? ` · ${ca.date}` : ''}
              </span>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function WatchlistPage() {
  const { items, loading, remove } = useWatchlist();
  const [prices, setPrices] = useState<Record<string, LivePrice>>({});
  const [pricesLoading, setPricesLoading] = useState(true);
  const calendarEntries = useWatchlistCalendar(items.map(i => i.symbol));

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
          <h1 className="text-4xl font-black tracking-tight text-tx mb-1.5">Watchlist</h1>
          <p className="text-muted text-sm max-w-xl leading-relaxed">
            Stocks you&apos;ve starred from stock analysis, Market Picks, or SME Signals, all in one
            place. Tied to this browser only — it won&apos;t follow you to another device yet.
          </p>
        </div>

        <CalendarStrip entries={calendarEntries} />

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
                          <Link href={`/?symbol=${encodeURIComponent(item.symbol)}`} className="font-semibold text-tx hover:text-accent transition-colors text-sm">
                            {item.symbol}
                          </Link>
                        </td>
                        <td className="px-4 py-4 max-w-[220px]">
                          <span className="text-muted text-xs truncate block" title={item.company}>
                            {item.company}
                          </span>
                        </td>
                        <td className="px-4 py-4">
                          <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border ${
                            item.exchange === 'BSE'
                              ? 'text-hold border-hold/20 bg-hold/10'
                              : 'text-buy border-buy/20 bg-buy/10'
                          }`}>
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
