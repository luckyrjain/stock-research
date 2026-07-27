'use client';

import { useEffect, useState } from 'react';
import type { VerdictHistoryEntry, VerdictHistoryResponse } from '@/types';
import { fmt } from './dashboard-format';

function useVerdictHistory(symbol: string): VerdictHistoryResponse | null {
  const [data, setData] = useState<VerdictHistoryResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    fetch(`/api/verdict-history/${encodeURIComponent(symbol)}`)
      .then(res => (res.ok ? res.json() : null))
      .then((data: VerdictHistoryResponse | null) => { if (!cancelled) setData(data); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [symbol]);

  return data;
}

const TIMELINE_REC_CLS: Record<string, string> = {
  BUY:  'bg-buy/12 text-buy border-buy/25',
  HOLD: 'bg-hold/12 text-hold border-hold/25',
  SELL: 'bg-sell/12 text-sell border-sell/25',
};

// How today's call compares to past ones for the same stock — a strip of the
// daily verdict snapshots verdict_history.save_snapshot() writes after every
// analysis run. Needs at least two points to be a "timeline" at all, so a
// symbol analysed for the first time today renders nothing here. Each badge
// also carries a win/loss mark scored against today's live price (BUY/SELL
// only — a HOLD makes no directional claim, so it's never graded), the
// single-stock analogue of the win-rate Market Picks already tracks for
// itself.
export default function VerdictTimeline({ symbol }: { symbol: string }) {
  const data = useVerdictHistory(symbol);
  const history = data?.history ?? [];
  if (history.length < 2) return null;

  return (
    <div className="px-6 py-2.5 border-t border-border/60">
      <div className="flex items-center gap-3 overflow-x-auto">
        <span className="text-[10px] font-semibold text-muted uppercase tracking-wider shrink-0">
          Verdict Timeline
          {data?.scored_count ? (
            <span className="ml-1.5 font-normal normal-case text-muted/70">
              &middot; {data.win_rate}% right so far ({data.scored_count} scored)
            </span>
          ) : null}
        </span>
        <div className="flex items-center gap-1.5 min-w-0">
          {history.map((h, i) => {
            const isLast = i === history.length - 1;
            const cls = (h.recommendation && TIMELINE_REC_CLS[h.recommendation]) || 'bg-card-hi text-muted border-border';
            const outcomeMark = h.outcome === 'win' ? '✓' : h.outcome === 'loss' ? '✗' : null;
            const tooltip = [
              h.date,
              h.current_price != null ? `₹${fmt(h.current_price)}` : null,
              h.confidence ? `${h.confidence} confidence` : null,
              h.signal_score != null ? `signal score ${fmt(h.signal_score, 2)}` : null,
              h.return_since_pct != null
                ? `${h.return_since_pct >= 0 ? '+' : ''}${h.return_since_pct}% since`
                : null,
            ].filter(Boolean).join(' · ');
            return (
              <div key={h.date} className="flex items-center gap-1.5 shrink-0">
                {i > 0 && <span className="text-muted/30 text-xs">→</span>}
                <span
                  title={tooltip}
                  className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold border whitespace-nowrap ${cls} ${
                    isLast ? 'ring-1 ring-accent/40' : ''
                  }`}
                >
                  {h.recommendation ?? '—'}
                  <span className="font-normal opacity-70">
                    {/* h.date is a bare 'YYYY-MM-DD' string, which Date parses
                        as UTC midnight -- rendering without an explicit
                        timeZone would use the browser's LOCAL timezone and
                        silently shift the displayed date back by one day for
                        any visitor west of UTC. timeZone: 'UTC' keeps the
                        render matching the calendar date the string encodes. */}
                    {new Date(h.date).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'UTC' })}
                  </span>
                  {outcomeMark && (
                    <span
                      aria-label={h.outcome === 'win' ? 'Correct so far' : 'Incorrect so far'}
                      className={h.outcome === 'win' ? 'text-buy' : 'text-sell'}
                    >
                      {outcomeMark}
                    </span>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
