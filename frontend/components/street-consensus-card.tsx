'use client';

import { useEffect, useState } from 'react';
import type { StreetConsensus } from '@/types';
import InfoTooltip from './info-tooltip';
import { Card } from './dashboard-primitives';
import { safeExternalHref } from './dashboard-format';

function fmtConsensusDate(publishedAt: string | null): string | null {
  if (!publishedAt) return null;
  const d = new Date(publishedAt);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

export function useStreetConsensus(symbol: string): StreetConsensus | null {
  const [consensus, setConsensus] = useState<StreetConsensus | null>(null);

  useEffect(() => {
    let cancelled = false;
    setConsensus(null);
    fetch(`/api/street-consensus/${encodeURIComponent(symbol)}`)
      .then(res => (res.ok ? res.json() : null))
      .then((data: StreetConsensus | null) => { if (!cancelled) setConsensus(data); })
      .catch(() => { if (!cancelled) setConsensus(null); });
    return () => { cancelled = true; };
  }, [symbol]);

  return consensus;
}

function _consensusRatingTone(rating: string): string {
  if (rating.includes('BUY')) return 'text-buy border-buy/25 bg-buy/10';
  if (rating.includes('SELL')) return 'text-sell border-sell/25 bg-sell/10';
  return 'text-hold border-hold/25 bg-hold/10';
}

// Real numeric analyst consensus scraped directly from Trendlyne's own
// company page — additive to the article list below, never a replacement.
// Every field is independently null when Trendlyne's page doesn't cleanly
// present it, so this renders only the fields it actually has.
function NumericConsensusRow({ numeric }: { numeric: NonNullable<StreetConsensus['numeric_consensus']> }) {
  const { analyst_count, consensus_rating, mean_target_price, target_upside_pct, source_url } = numeric;
  if (analyst_count == null && consensus_rating == null && mean_target_price == null && target_upside_pct == null) return null;

  // Defense in depth, independent of the backend's own host validation in
  // tools/trendlyne_scraper.py::_is_trendlyne_host — this component
  // shouldn't rely on backend behavior it doesn't itself verify before
  // rendering an external link.
  const safeSourceUrl = source_url && source_url.startsWith('https://trendlyne.com/') ? source_url : undefined;

  return (
    <a
      href={safeSourceUrl}
      target={safeSourceUrl ? '_blank' : undefined}
      rel={safeSourceUrl ? 'noopener noreferrer' : undefined}
      className={`flex flex-wrap items-center gap-2 mb-3 pb-3 border-b border-card-hi/60 text-xs ${safeSourceUrl ? 'hover:opacity-80 transition-opacity' : ''}`}
    >
      {consensus_rating && (
        <span className={`px-2 py-0.5 rounded-full border font-semibold ${_consensusRatingTone(consensus_rating)}`}>
          {consensus_rating}
        </span>
      )}
      {analyst_count != null && (
        <span className="text-muted">{analyst_count} analyst{analyst_count === 1 ? '' : 's'}</span>
      )}
      {mean_target_price != null ? (
        <span className="text-tx">
          Mean target ₹{mean_target_price.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
          {target_upside_pct != null && (
            <span className={target_upside_pct >= 0 ? 'text-buy' : 'text-sell'}>
              {' '}({target_upside_pct >= 0 ? '+' : ''}{target_upside_pct.toFixed(1)}%)
            </span>
          )}
        </span>
      ) : target_upside_pct != null ? (
        <span className={target_upside_pct >= 0 ? 'text-buy' : 'text-sell'}>
          {target_upside_pct >= 0 ? '+' : ''}{target_upside_pct.toFixed(1)}% {target_upside_pct >= 0 ? 'upside' : 'downside'}
        </span>
      ) : null}
    </a>
  );
}

// Recent Trendlyne-cited analyst commentary for this stock — real article
// titles/links/dates from GNews coverage, plus (when available) a real
// numeric consensus scraped directly from Trendlyne's own company page
// (see tools/trendlyne_scraper.py) — analyst count, consensus rating, mean
// target price. Renders nothing when there's neither recent coverage nor a
// resolvable numeric consensus — the expected common case for most stocks
// on most days, not missing data.
export function StreetConsensusCard({ consensus }: { consensus: StreetConsensus | null }) {
  const numeric = consensus?.numeric_consensus;
  const hasNumeric = numeric != null && (numeric.analyst_count != null || numeric.consensus_rating != null || numeric.mean_target_price != null || numeric.target_upside_pct != null);
  const articlesUnavailable = consensus?.articles_unavailable ?? false;
  const numericUnavailable = consensus?.numeric_consensus_unavailable ?? false;
  if (!consensus || (consensus.articles.length === 0 && !hasNumeric && !articlesUnavailable && !numericUnavailable)) return null;

  return (
    <Card title={<>
      Street Consensus
      <InfoTooltip title="Street Consensus" align="left">
        <p>Real analyst count / consensus rating / mean target price scraped directly from Trendlyne&apos;s own company page, plus recent news coverage that cites Trendlyne&apos;s consensus for this stock — upgrades, initiations, and target-price moves.</p>
        <p>Every field is independently blank when Trendlyne doesn&apos;t cleanly present it — AlphaPulse never invents a benchmark it doesn&apos;t actually have.</p>
      </InfoTooltip>
    </>}>
      {!hasNumeric && numericUnavailable && (
        <p className="text-xs text-muted italic mb-2">Numeric consensus temporarily unavailable — Trendlyne fetch failed, try again shortly.</p>
      )}
      {consensus.articles.length === 0 && articlesUnavailable && (
        <p className="text-xs text-muted italic mb-2">Recent coverage temporarily unavailable — fetch failed, try again shortly.</p>
      )}
      {hasNumeric && numeric && <NumericConsensusRow numeric={numeric} />}
      <div className="space-y-2.5">
        {consensus.articles.slice(0, 6).map((a, i) => {
          const date = fmtConsensusDate(a.published_at);
          const safeUrl = safeExternalHref(a.url);
          return (
            <a
              key={i}
              href={safeUrl}
              target={safeUrl ? '_blank' : undefined}
              rel={safeUrl ? 'noopener noreferrer' : undefined}
              className="block text-xs hover:bg-card-hi/60 rounded-lg px-2 py-1.5 -mx-2 transition-colors"
            >
              <p className="text-tx line-clamp-2">{a.title}</p>
              {date && <p className="text-muted/60 mt-0.5">{date}</p>}
            </a>
          );
        })}
      </div>
    </Card>
  );
}
