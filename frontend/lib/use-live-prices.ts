'use client';

import { useEffect, useState } from 'react';

// watchlist/page.tsx, portfolio/page.tsx, and positions-strip.tsx each
// independently implemented the same live-price fetch/poll/staleness state
// machine against GET /api/prices: a local `LivePrice` interface, a 30s
// `setInterval` poll guarded by a `cancelled` flag, and `stale`/`updatedAt`
// state (design.md's five-states rule: a poll that's been failing must not
// render identically to one that's fresh). This is the one implementation —
// each caller keeps its own rendering (and, for watchlist/portfolio, the
// shared LTP/stale indicator now lives in live-price-status.tsx).
//
// Deliberately a plain hook, not a createSharedResource() singleton (see
// lib/watchlist.ts) — each caller polls a different, independently-changing
// symbol set (watched symbols, held positions, ...), so there is no single
// shared cache key to dedupe against.
export interface LivePrice {
  // GET /api/prices returns an entry for every requested symbol, but it's
  // `{}` (not omitted, not null) when the price lookup itself failed (e.g.
  // yfinance had nothing for either the .NS or .BO suffix) — so `prices[sym]`
  // being truthy does NOT imply these fields are actually present.
  price?: number;
  // null when the price resolved but yfinance had no previous_close to
  // diff against — a real "change unknown", not a fabricated flat 0%.
  change_pct?: number | null;
}

interface UseLivePricesResult {
  prices: Record<string, LivePrice>;
  stale: boolean;
  updatedAt: Date | null;
  loading: boolean;
}

export function useLivePrices(symbols: string[]): UseLivePricesResult {
  const [prices, setPrices] = useState<Record<string, LivePrice>>({});
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [stale, setStale] = useState(false);
  const [loading, setLoading] = useState(true);

  const key = symbols.join(',');

  useEffect(() => {
    if (!key) { setLoading(false); return; }
    let cancelled = false;

    const fetchPrices = async () => {
      try {
        const res = await fetch(`/api/prices?symbols=${encodeURIComponent(key)}`);
        if (!res.ok) { if (!cancelled) setStale(true); return; }
        const data = await res.json() as { prices: Record<string, LivePrice> };
        if (!cancelled) {
          setPrices(data.prices);
          setUpdatedAt(new Date());
          setStale(false);
        }
      } catch {
        // Row/card still shows "—" for a symbol with no price yet — but once
        // there IS a price on screen, a failed poll marks it stale rather
        // than silently leaving it looking fresh forever.
        if (!cancelled) setStale(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    fetchPrices();
    const id = setInterval(fetchPrices, 30_000);
    return () => { cancelled = true; clearInterval(id); };
  }, [key]);

  return { prices, stale, updatedAt, loading };
}
