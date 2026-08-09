'use client';

import { useEffect, useState } from 'react';
import type { PriceHistory } from '@/types';
import Sparkline from './sparkline';
import { fmt } from '@/lib/format';

export default function PriceSparkline({ symbol }: { symbol: string }) {
  const [history, setHistory] = useState<PriceHistory | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/prices/history/${encodeURIComponent(symbol)}?days=180&benchmark=true`)
      .then(res => (res.ok ? res.json() : null))
      .then((data: PriceHistory | null) => { if (!cancelled) setHistory(data); })
      .catch(() => { if (!cancelled) setHistory(null); });
    return () => { cancelled = true; };
  }, [symbol]);

  if (!history || history.closes.length < 2) return null;
  const bench = history.benchmark;

  return (
    <div className="flex flex-col items-end gap-1 shrink-0">
      <span className="text-[9px] font-semibold text-muted uppercase tracking-wider">6M trend</span>
      <Sparkline
        closes={history.closes}
        dates={history.dates}
        width={110}
        height={30}
        formatValue={v => `₹${fmt(v, 2)}`}
      />
      {bench && (
        <span
          className={`text-[10px] font-mono ${bench.alpha_pct >= 0 ? 'text-buy' : 'text-sell'}`}
          title={`Stock ${bench.stock_change_pct >= 0 ? '+' : ''}${bench.stock_change_pct}% vs. Nifty ${bench.nifty_change_pct >= 0 ? '+' : ''}${bench.nifty_change_pct}% over the same window`}
        >
          {bench.alpha_pct >= 0 ? '+' : ''}{bench.alpha_pct}% vs Nifty
        </span>
      )}
    </div>
  );
}
