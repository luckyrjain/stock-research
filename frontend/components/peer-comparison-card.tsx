'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import type { PeerComparison } from '@/types';
import { Card } from './dashboard-primitives';

export function usePeerComparison(symbol: string): PeerComparison | null {
  const [peers, setPeers] = useState<PeerComparison | null>(null);

  useEffect(() => {
    let cancelled = false;
    setPeers(null);
    fetch(`/api/peers/${encodeURIComponent(symbol)}`)
      .then(res => (res.ok ? res.json() : null))
      .then((data: PeerComparison | null) => { if (!cancelled) setPeers(data); })
      .catch(() => { if (!cancelled) setPeers(null); });
    return () => { cancelled = true; };
  }, [symbol]);

  return peers;
}

export function PercentileBadge({ value }: { value: number }) {
  return (
    <span
      title={`${Math.round(value)}th percentile among sector peers`}
      className="ml-1.5 text-[9px] font-semibold px-1.5 py-0.5 rounded-full
                 bg-accent/10 text-accent border border-accent/20 whitespace-nowrap"
    >
      {Math.round(value)}th pct
    </span>
  );
}

// Where the stock's current P/E sits within its OWN last 3-5 years of
// Screener-published P/E — an absolute anchor alongside PercentileBadge's
// peer-relative reading above. Low percentile (cheap vs. its own history)
// reads as buy-toned, high (expensive vs. its own history) as sell-toned,
// same color convention as the BUY/HOLD/SELL badges elsewhere in this file.
export function ValuationAnchorBadge({ anchor }: { anchor: PeerComparison['absolute_anchor'] }) {
  if (!anchor || anchor.years.length === 0) return null;
  const tone = anchor.percentile <= 33 ? 'text-buy border-buy/25 bg-buy/10'
    : anchor.percentile >= 67 ? 'text-sell border-sell/25 bg-sell/10'
    : 'text-hold border-hold/25 bg-hold/10';
  return (
    <div
      className={`mt-3 rounded-lg border px-3 py-2 text-xs ${tone}`}
      title={`Current P/E ${anchor.current_pe} vs. its own ${anchor.years[0]}–${anchor.years[anchor.years.length - 1]} range (low ${anchor.low}, median ${anchor.median}, high ${anchor.high})`}
    >
      <span className="font-semibold">P/E {anchor.current_pe}</span>
      {' — '}
      {Math.round(anchor.percentile)}th percentile of its own {anchor.years.length}-year range
      {' '}
      <span className="text-muted">({anchor.low}–{anchor.high}, median {anchor.median})</span>
    </div>
  );
}

export function PeerTable({ peers }: { peers: PeerComparison | null }) {
  if (!peers || (!peers.self && peers.peers.length === 0)) return null;

  const rows = [
    ...(peers.self ? [{ ...peers.self, kind: 'self' as const }] : []),
    ...peers.peers.map(p => ({ ...p, kind: 'peer' as const })),
    ...(peers.sector_median ? [{ ...peers.sector_median, kind: 'median' as const }] : []),
  ];
  if (rows.length === 0) return null;

  // Column set is sector-dependent (Screener's own table drives it) — derive it
  // from whichever rows actually have values, in first-seen order, skipping the
  // row-label column ("Name") and any blank header (Screener's leading S.No. column).
  const columns: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row.values)) {
      if (key && key !== 'Name' && !columns.includes(key)) columns.push(key);
    }
  }
  if (columns.length === 0) return null;

  return (
    <Card title="Peer Comparison">
      <div className="overflow-x-auto -mx-1">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left font-semibold text-muted px-1 py-1.5 whitespace-nowrap">Name</th>
              {columns.map(col => (
                <th key={col} className="text-right font-semibold text-muted px-1 py-1.5 whitespace-nowrap">{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr
                key={`${row.kind}-${row.name}-${i}`}
                className={`border-b border-border/60 last:border-0 ${row.kind === 'self' ? 'bg-accent/5' : ''}`}
              >
                <td className={`px-1 py-1.5 whitespace-nowrap ${row.kind === 'self' ? 'font-semibold text-accent' : row.kind === 'median' ? 'text-muted italic' : 'text-tx'}`}>
                  {row.name}
                </td>
                {columns.map(col => (
                  <td key={col} className="text-right px-1 py-1.5 font-mono text-tx whitespace-nowrap">
                    {row.values[col] ?? '—'}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {peers.peers.length === 0 && (
        <p className="text-xs text-muted mt-2">
          Screener doesn&apos;t list sector peers for this stock, or the peer comparison table wasn&apos;t available.
        </p>
      )}
      <ValuationAnchorBadge anchor={peers.absolute_anchor} />
    </Card>
  );
}

// Lightweight "you might also look at" rail off the same peer list the Peer
// Comparison table already fetches — no new data source. Screener's own peer
// `slug` is usually the NSE ticker itself, but that's not guaranteed for
// every listing (a few Screener slugs diverge from the tradable symbol) — a
// bad deep link degrades to this app's existing "couldn't resolve/analyse
// that symbol" error state on the destination page, the same as a mistyped
// manual search, rather than silently failing here.
export function SimilarStocksRail({ peers }: { peers: PeerComparison | null }) {
  if (!peers || peers.peers.length === 0) return null;
  return (
    <Card title="Similar Stocks">
      <div className="flex flex-wrap gap-2">
        {peers.peers.map(p => (
          <Link
            key={p.slug || p.name}
            href={`/?symbol=${encodeURIComponent(p.slug || p.name)}`}
            className="text-xs font-medium px-2.5 py-1 rounded-full border border-border bg-surface
                       text-tx hover:border-accent/40 hover:text-accent transition-colors"
          >
            {p.name}
          </Link>
        ))}
      </div>
    </Card>
  );
}
