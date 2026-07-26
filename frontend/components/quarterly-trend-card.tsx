'use client';

import type { QuarterlyTrend } from '@/types';
import Sparkline from './sparkline';
import { Card } from './dashboard-primitives';
import { fmt } from './dashboard-format';

// Two mini-sparklines (Revenue, EPS) over the last few quarters — reuses the
// Sparkline component built for the price-history strip in the hero, just
// fed a different numeric series each time.
export default function QuarterlyTrendCard({ trend }: { trend: QuarterlyTrend | undefined }) {
  if (!trend || trend.quarters.length < 2) return null;
  const latest = trend.quarters[trend.quarters.length - 1];

  return (
    <Card title="Quarterly Trend">
      <p className="text-[11px] text-muted mb-3">Last {trend.quarters.length} quarters, through {latest}</p>
      <div className="space-y-4">
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-sm text-muted">Revenue</span>
            <span className="font-mono font-semibold text-tx text-sm">
              ₹{fmt(trend.revenue[trend.revenue.length - 1], 0)} Cr
            </span>
          </div>
          <Sparkline
            closes={trend.revenue}
            dates={trend.quarters}
            width={220}
            height={32}
            ariaLabel={`Quarterly revenue trend over the last ${trend.quarters.length} quarters`}
            formatValue={v => `₹${fmt(v, 0)} Cr`}
          />
        </div>
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-sm text-muted">EPS</span>
            <span className="font-mono font-semibold text-tx text-sm">
              ₹{fmt(trend.eps[trend.eps.length - 1], 2)}
            </span>
          </div>
          <Sparkline
            closes={trend.eps}
            dates={trend.quarters}
            width={220}
            height={32}
            ariaLabel={`Quarterly EPS trend over the last ${trend.quarters.length} quarters`}
            formatValue={v => `₹${fmt(v, 2)}`}
          />
        </div>
        {trend.operating_margin && trend.operating_margin.length === trend.quarters.length && (
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-sm text-muted">Operating Margin</span>
              <span className="font-mono font-semibold text-tx text-sm">
                {fmt(trend.operating_margin[trend.operating_margin.length - 1], 1)}%
              </span>
            </div>
            <Sparkline
              closes={trend.operating_margin}
              dates={trend.quarters}
              width={220}
              height={32}
              ariaLabel={`Quarterly operating margin trend over the last ${trend.quarters.length} quarters`}
              formatValue={v => `${fmt(v, 1)}%`}
            />
          </div>
        )}
      </div>
    </Card>
  );
}
