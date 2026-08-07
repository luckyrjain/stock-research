'use client';

import { useEffect, useState } from 'react';
import type { InsiderActivity } from '@/types';
import InfoTooltip from './info-tooltip';
import { Card } from './dashboard-primitives';
import { fmtInr } from '@/lib/format';

function fmtActivityDate(dateIso: string | null, fallback: string): string {
  if (!dateIso) return fallback;
  return new Date(dateIso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

function ActionBadge({ action }: { action: 'BUY' | 'SELL' }) {
  return (
    <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border whitespace-nowrap ${
      action === 'BUY' ? 'bg-buy/12 text-buy border-buy/25' : 'bg-sell/12 text-sell border-sell/25'
    }`}>
      {action}
    </span>
  );
}

// Neutral, always-visible tag — distinct from ActionBadge's buy/sell tone —
// for a plain classification (insider category, Bulk vs. Block deal type)
// that previously only ever surfaced on hover via the row's `title` attribute.
function TagBadge({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded border border-border text-muted whitespace-nowrap">
      {children}
    </span>
  );
}

function useInsiderActivity(symbol: string): InsiderActivity | null {
  const [activity, setActivity] = useState<InsiderActivity | null>(null);

  useEffect(() => {
    let cancelled = false;
    setActivity(null);
    fetch(`/api/insider-activity/${encodeURIComponent(symbol)}`)
      .then(res => (res.ok ? res.json() : null))
      .then((data: InsiderActivity | null) => { if (!cancelled) setActivity(data); })
      .catch(() => { if (!cancelled) setActivity(null); });
    return () => { cancelled = true; };
  }, [symbol]);

  return activity;
}

// Promoter/director insider trades and institutional bulk/block deals — the
// same NSE feeds Market Picks already scrapes for discovery, surfaced here
// directly for whichever one stock a researcher is looking at, instead of
// only ever showing up (via LLM extraction) when a stock happens to make the
// weekly picks list. Renders nothing when NSE has nothing for this symbol —
// most stocks won't have recent activity, which is the expected common case.
export function InsiderActivityCard({ symbol }: { symbol: string }) {
  const activity = useInsiderActivity(symbol);
  if (!activity) return null;
  const {
    insider_trades: trades, bulk_block_deals: deals,
    insider_trades_unavailable: tradesUnavailable, bulk_block_deals_unavailable: dealsUnavailable,
  } = activity;
  // A real scrape failure is rendered as a notice, not silence — an empty
  // array with no failure (the expected common case) still renders nothing,
  // same as before this distinction existed.
  if (trades.length === 0 && deals.length === 0 && !tradesUnavailable && !dealsUnavailable) return null;

  return (
    <Card title={<>
      Insider &amp; Institutional Activity
      <InfoTooltip title="Insider & Institutional Activity" align="left">
        <p>Promoter/director trades disclosed to NSE (PIT filings) and large institutional bulk/block deals for this stock.</p>
        <p>Most stocks have no recent activity — that&apos;s the common case, not missing data.</p>
      </InfoTooltip>
    </>}>
      {trades.length === 0 && tradesUnavailable && (
        <p className="text-xs text-muted italic mb-2">Insider trades temporarily unavailable — NSE fetch failed, try again shortly.</p>
      )}
      {deals.length === 0 && dealsUnavailable && (
        <p className="text-xs text-muted italic mb-2">Bulk &amp; block deals temporarily unavailable — NSE fetch failed, try again shortly.</p>
      )}
      {trades.length > 0 && (
        <div className={deals.length > 0 ? 'mb-4' : ''}>
          <p className="text-[10px] font-semibold text-muted uppercase tracking-wider mb-2">Insider Trades</p>
          <div className="space-y-2">
            {trades.map((t, i) => (
              <div key={i} className="flex items-center justify-between gap-2 text-xs">
                <div className="min-w-0 flex items-center gap-1.5">
                  <ActionBadge action={t.action} />
                  {t.category && <TagBadge>{t.category}</TagBadge>}
                  <span className="text-tx truncate">{t.person}</span>
                </div>
                <div className="flex items-center gap-2 shrink-0 font-mono text-muted">
                  <span>{fmtInr(t.value)}</span>
                  <span className="text-muted/60">{fmtActivityDate(t.date_iso, t.date)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {deals.length > 0 && (
        <div>
          <p className="text-[10px] font-semibold text-muted uppercase tracking-wider mb-2">Bulk &amp; Block Deals</p>
          <div className="space-y-2">
            {deals.map((d, i) => (
              <div key={i} className="flex items-center justify-between gap-2 text-xs">
                <div className="min-w-0 flex items-center gap-1.5">
                  <ActionBadge action={d.action} />
                  <TagBadge>{d.deal_type === 'Block Deal' ? 'Block' : 'Bulk'}</TagBadge>
                  <span className="text-tx truncate">{d.client}</span>
                </div>
                <div className="flex items-center gap-2 shrink-0 font-mono text-muted">
                  <span>{fmtInr(d.price * d.quantity)}</span>
                  <span className="text-muted/60">{fmtActivityDate(d.date_iso, d.date)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}
