'use client';

import type { StockInfo } from '@/types';
import { fmt } from '@/lib/format';
import { PercentileBadge } from './peer-comparison-card';

export function Card({ title, children, className = '' }: { title: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <div className={`bg-card border border-border rounded-xl p-5 ${className}`}>
      <p className="text-[11px] font-semibold text-muted tracking-[1px] uppercase mb-3 flex items-center gap-1.5">{title}</p>
      {children}
    </div>
  );
}

export function MetricRow({ label, value, colorClass = 'text-tx', percentile }: {
  label: React.ReactNode; value: string; colorClass?: string; percentile?: number;
}) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-border last:border-0">
      <span className="text-sm text-muted flex items-center gap-1">{label}</span>
      <span className="flex items-center">
        <span className={`text-sm font-semibold font-mono ${colorClass}`}>{value}</span>
        {percentile != null && <PercentileBadge value={percentile} />}
      </span>
    </div>
  );
}

export function ExchangeTable({
  quotes,
  primaryExchange,
}: {
  quotes: Array<[string, Partial<StockInfo>]>;
  primaryExchange: string;
}) {
  if (quotes.length === 1) {
    const [exchange, q] = quotes[0];
    const change    = q?.change_pct ?? 0;
    const changeCls = change > 0 ? 'text-buy' : change < 0 ? 'text-sell' : 'text-muted';
    return (
      <div className="text-right shrink-0">
        <p
          className="text-2xl font-bold font-mono text-tx"
          title={q?.previous_close != null ? `Previous close: ₹${fmt(q.previous_close)}` : undefined}
        >
          {q?.current_price != null ? `₹${fmt(q.current_price)}` : '—'}
        </p>
        <p className={`text-sm font-mono ${changeCls}`}>
          {`${change > 0 ? '+' : ''}${fmt(change)}%`}
        </p>
        <p className="text-[11px] text-muted mt-0.5">{exchange}</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden text-sm min-w-[260px] shrink-0">
      {quotes.map(([exchange, q]) => {
        const change    = q?.change_pct ?? 0;
        const changeCls = change > 0 ? 'text-buy' : change < 0 ? 'text-sell' : 'text-muted';
        const isPrimary = exchange === primaryExchange;
        return (
          <div
            key={exchange}
            className={`flex items-center justify-between px-4 py-2.5 border-b border-border last:border-0 ${
              isPrimary ? 'bg-accent/5' : ''
            }`}
          >
            <div className="flex items-center gap-2">
              <span className="font-mono text-[11px] font-semibold text-muted">{exchange}</span>
              {isPrimary && (
                <span className="text-[10px] font-semibold uppercase tracking-wide text-accent">Primary</span>
              )}
            </div>
            <div className="text-right">
              <span
                className="font-mono font-bold text-tx"
                title={q?.previous_close != null ? `Previous close: ₹${fmt(q.previous_close)}` : undefined}
              >
                {q?.current_price != null ? `₹${fmt(q.current_price)}` : '—'}
              </span>
              <span className={`ml-3 font-mono text-xs ${changeCls}`}>
                {`${change > 0 ? '+' : ''}${fmt(change)}%`}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function RangeBar({ low, current, high }: { low: number; current: number; high: number }) {
  const pct = high === low ? 50 : Math.max(0, Math.min(100, ((current - low) / (high - low)) * 100));
  return (
    <div className="mt-3">
      <div className="relative h-1.5 rounded-full bg-border">
        <div className="absolute inset-0 rounded-full bg-gradient-to-r from-sell/25 via-hold/25 to-buy/25" />
        <div
          className="absolute top-1/2 w-2 h-2 rounded-full bg-tx border border-bg"
          style={{ left: `${pct}%`, transform: 'translate(-50%, -50%)' }}
        />
      </div>
      <div className="flex justify-between text-[10px] text-muted/60 mt-1">
        <span>₹{fmt(low, 0)}</span>
        <span>52W Range · {Math.round(pct)}%</span>
        <span>₹{fmt(high, 0)}</span>
      </div>
    </div>
  );
}
