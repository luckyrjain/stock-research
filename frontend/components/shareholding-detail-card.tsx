'use client';

import { useEffect, useState } from 'react';
import type { ShareholdingDetail } from '@/types';
import InfoTooltip from './info-tooltip';
import { Card } from './dashboard-primitives';
import { fmt } from './dashboard-format';

function useShareholdingDetail(symbol: string): ShareholdingDetail | null {
  const [detail, setDetail] = useState<ShareholdingDetail | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    fetch(`/api/shareholding-detail/${encodeURIComponent(symbol)}`)
      .then(res => (res.ok ? res.json() : null))
      .then((data: ShareholdingDetail | null) => { if (!cancelled) setDetail(data); })
      .catch(() => { if (!cancelled) setDetail(null); });
    return () => { cancelled = true; };
  }, [symbol]);

  return detail;
}

function HolderRow({ name, holdingPct }: { name: string; holdingPct: number }) {
  return (
    <div className="flex items-center justify-between gap-2 text-xs py-1">
      <span className="text-tx truncate">{name}</span>
      <span className="font-mono font-semibold text-accent shrink-0">{fmt(holdingPct, 2)}%</span>
    </div>
  );
}

// Granular, individually-named shareholder detail — promoters by name, plus
// every other named-shareholder category NSE's own shareholding XBRL filing
// discloses (mutual funds, FPIs, insurance companies, whatever the filing
// actually contains). A more granular complement to the existing aggregate
// "Shareholding Pattern" card (category percentages only) and "Mutual Fund
// Holdings" card (mutual funds only) — this shows *who*, not just *how much
// of which broad category*. Renders nothing when the fetch hasn't resolved
// or found genuinely nothing (the common case for filings with no
// individually-named holders above the plausibility threshold); renders an
// "unavailable" notice, not silence, for a real scrape failure.
export function ShareholdingDetailCard({ symbol }: { symbol: string }) {
  const detail = useShareholdingDetail(symbol);
  if (!detail) return null;
  const { promoters, shareholder_categories: categories, unavailable, as_of_date } = detail;
  if (promoters.length === 0 && categories.length === 0 && !unavailable) return null;

  return (
    <Card title={<>
      Detailed Shareholding
      <InfoTooltip title="Detailed Shareholding" align="left">
        <p>Every individually-named shareholder disclosed in this company&apos;s most recent NSE shareholding filing — named promoters with their own holding %, plus every other named-shareholder category the filing contains (mutual funds, foreign portfolio investors, insurance companies, and so on).</p>
        <p>Category labels come directly from NSE&apos;s own filing, not a fixed list this app maintains — a category that doesn&apos;t appear here simply wasn&apos;t individually named in the filing, which is expected for most small/aggregate holders.</p>
      </InfoTooltip>
    </>}>
      {unavailable && promoters.length === 0 && categories.length === 0 ? (
        <p className="text-xs text-muted italic">Detailed shareholding temporarily unavailable — NSE fetch failed, try again shortly.</p>
      ) : (
        <>
          {as_of_date && <p className="text-[10px] text-muted/70 mb-3">As of {as_of_date}</p>}

          {promoters.length > 0 && (
            <div className={categories.length > 0 ? 'mb-4' : ''}>
              <p className="text-[10px] font-semibold text-muted uppercase tracking-wider mb-1.5">Promoters</p>
              <div className="divide-y divide-border">
                {promoters.map((p, i) => <HolderRow key={i} name={p.name} holdingPct={p.holding_pct} />)}
              </div>
            </div>
          )}

          {categories.map((cat, i) => (
            <div key={cat.category} className={i < categories.length - 1 ? 'mb-4' : ''}>
              <p className="text-[10px] font-semibold text-muted uppercase tracking-wider mb-1.5">{cat.category}</p>
              <div className="divide-y divide-border">
                {cat.holders.map((h, j) => <HolderRow key={j} name={h.name} holdingPct={h.holding_pct} />)}
              </div>
            </div>
          ))}
        </>
      )}
    </Card>
  );
}
