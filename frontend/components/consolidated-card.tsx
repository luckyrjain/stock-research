'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import type { ConsolidatedView } from '@/types';

interface Props {
  symbol: string;
  onClose: () => void;
}

const REC_BADGE: Record<string, string> = {
  BUY:       'bg-buy/12 text-buy border-buy/25',
  WATCHLIST: 'bg-buy/8 text-buy/75 border-buy/15',
  HOLD:      'bg-hold/12 text-hold border-hold/25',
  SELL:      'bg-sell/12 text-sell border-sell/25',
};

function RecBadge({ rec }: { rec: string }) {
  const cls = REC_BADGE[rec] ?? 'bg-muted/10 text-muted border-muted/20';
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold border tracking-wide ${cls}`}>
      {rec}
    </span>
  );
}

function fmtAsOf(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' });
}

// Analysis is a 3-tier BUY/HOLD/SELL verdict; Market Picks is a separate
// 4-tier BUY/WATCHLIST/HOLD/SELL score (see CLAUDE.md's "4-tier
// recommendation in market picks"). The two run independently and can
// legitimately disagree — this used to just stack both badges with no
// acknowledgement that they might not match.
function verdictsDisagree(analysisRec: string | null, pickRec: string | null): boolean {
  if (!analysisRec || !pickRec) return false;
  if (pickRec === 'WATCHLIST') return analysisRec !== 'BUY';
  return analysisRec !== pickRec;
}

function SectionSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="h-16 rounded-lg bg-border/40 animate-pulse" />
      ))}
    </div>
  );
}

// Pure client-side aggregation view: fetches GET /api/consolidated/{symbol},
// which itself just reads whatever stock analysis / market picks / SME
// signals have already cached for this symbol — no new data is fetched here
// either. Renders as a centered modal per design.md's "glass card... for
// modals" guidance, reusing the fixed-backdrop + Escape-to-close popover
// pattern already established by InfoTooltip.
export default function ConsolidatedCard({ symbol, onClose }: Props) {
  const [data, setData]       = useState<ConsolidatedView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(false);
  const closeBtnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    setData(null);
    fetch(`/api/consolidated/${encodeURIComponent(symbol)}`)
      .then(res => {
        if (!res.ok) throw new Error('request failed');
        return res.json() as Promise<ConsolidatedView>;
      })
      .then(body => { if (!cancelled) setData(body); })
      .catch(() => { if (!cancelled) setError(true); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [symbol]);

  useEffect(() => {
    closeBtnRef.current?.focus();
    const onKeyDown = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-40 flex items-start justify-center px-4 pt-24 sm:pt-32 bg-bg/80 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`AlphaPulse view for ${symbol}`}
        onClick={e => e.stopPropagation()}
        className="glass rounded-xl w-full max-w-lg max-h-[75vh] overflow-y-auto"
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-border/60 sticky top-0 bg-card/90 backdrop-blur-sm">
          <div>
            <p className="text-[10px] font-bold text-muted uppercase tracking-wider">What does AlphaPulse think about</p>
            <h2 className="font-mono font-black text-lg text-tx">{symbol}</h2>
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="text-muted hover:text-tx transition-colors text-xl leading-none px-2 py-1"
          >
            ×
          </button>
        </div>

        <div className="p-5 space-y-4">
          {loading && <SectionSkeleton />}

          {!loading && error && (
            <p className="text-sm text-sell">Couldn&apos;t reach AlphaPulse. Try again in a moment.</p>
          )}

          {!loading && !error && data && (
            <>
              {verdictsDisagree(data.analysis?.recommendation ?? null, data.market_pick?.recommendation ?? null) && (
                <p className="text-[11px] text-hold bg-hold/8 border border-hold/20 rounded-lg px-3 py-2 leading-relaxed">
                  Stock Analysis and Market Picks disagree here — they&apos;re two independent scoring
                  methods (3-tier vs. 4-tier), not a single source of truth, so this isn&apos;t a bug.
                </p>
              )}

              <section className="rounded-lg border border-border p-4">
                <div className="flex items-center justify-between gap-2 mb-2">
                  <p className="text-[10px] font-bold text-muted uppercase tracking-wider">Stock Analysis</p>
                  {fmtAsOf(data.analysis?.as_of) && (
                    <p className="text-[10px] text-muted/60">as of {fmtAsOf(data.analysis?.as_of)}</p>
                  )}
                </div>
                {data.analysis ? (
                  <>
                    <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                      {data.analysis.recommendation && <RecBadge rec={data.analysis.recommendation} />}
                      {data.analysis.confidence && (
                        <span className="text-xs text-muted">{data.analysis.confidence} confidence</span>
                      )}
                    </div>
                    {data.analysis.summary && (
                      <p className="text-sm text-tx leading-relaxed">{data.analysis.summary}</p>
                    )}
                  </>
                ) : (
                  <p className="text-sm text-muted">Not yet analyzed, or the cached analysis has expired.</p>
                )}
                <Link
                  href={`/?symbol=${encodeURIComponent(symbol)}`}
                  className="inline-block mt-2 text-xs font-semibold text-accent hover:underline"
                >
                  {data.analysis ? 'View full analysis →' : 'Run analysis →'}
                </Link>
              </section>

              <section className="rounded-lg border border-border p-4">
                <div className="flex items-center justify-between gap-2 mb-2">
                  <p className="text-[10px] font-bold text-muted uppercase tracking-wider">Market Picks</p>
                  {fmtAsOf(data.market_pick?.generated_at) && (
                    <p className="text-[10px] text-muted/60">as of {fmtAsOf(data.market_pick?.generated_at)}</p>
                  )}
                </div>
                {data.market_pick ? (
                  <>
                    <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                      {data.market_pick.recommendation && <RecBadge rec={data.market_pick.recommendation} />}
                      {data.market_pick.rank != null && (
                        <span className="text-xs text-muted">Rank #{data.market_pick.rank}</span>
                      )}
                      {data.market_pick.confidence_score != null && (
                        <span className="text-xs text-muted">{data.market_pick.confidence_score}/100 confidence</span>
                      )}
                    </div>
                    {data.market_pick.summary && (
                      <p className="text-sm text-tx leading-relaxed">{data.market_pick.summary}</p>
                    )}
                  </>
                ) : (
                  <p className="text-sm text-muted">Not currently on the weekly picks list.</p>
                )}
                <Link href="/market-picks" className="inline-block mt-2 text-xs font-semibold text-accent hover:underline">
                  View Market Picks →
                </Link>
              </section>

              <section className="rounded-lg border border-border p-4">
                <p className="text-[10px] font-bold text-muted uppercase tracking-wider mb-2">SME Signals</p>
                {data.sme ? (
                  <>
                    {data.sme.name && (
                      <p className="text-sm font-semibold text-tx mb-1">{data.sme.name}</p>
                    )}
                    <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold border tracking-wide
                        ${data.sme.in_golden_cross ? 'bg-buy/12 text-buy border-buy/25' : 'bg-sell/12 text-sell border-sell/25'}`}>
                        {data.sme.in_golden_cross ? 'GOLDEN REGIME' : 'DEATH REGIME'}
                      </span>
                      {data.sme.exchange && <span className="text-xs text-muted">{data.sme.exchange}</span>}
                    </div>
                    <p className="text-sm text-tx leading-relaxed">
                      {data.sme.cross ? `Last ${data.sme.cross} cross on ${data.sme.trade_date}.` : `As of ${data.sme.trade_date}.`}
                    </p>
                  </>
                ) : (
                  <p className="text-sm text-muted">
                    No SME regime data — likely not an NSE Emerge / BSE SME stock, or the screener hasn&apos;t run for it yet.
                  </p>
                )}
                <Link href="/sme-signals" className="inline-block mt-2 text-xs font-semibold text-accent hover:underline">
                  View SME Signals →
                </Link>
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
