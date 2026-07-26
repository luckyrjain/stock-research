'use client';

import type { ScreenerStock } from '@/types';
import InfoTooltip from './info-tooltip';

// A color-coded grid of NIFTY 500 industries, not a literal area-proportional
// treemap — this app has no charting library, and a real treemap layout
// engine would be a much bigger lift than the signal actually available
// here justifies. Sorted by aggregate market cap (biggest industries first)
// so size-by-position substitutes for size-by-area.
//
// Deliberately built from the SAME fields the Screener page already fetches
// and stores (ema_trend, rsi14, market_cap_cr) rather than a live day's %
// change — screener_stocks is a once-daily batch snapshot, not a live quote
// feed (see CLAUDE.md's "Custom screener flow"), so a live intraday
// heatmap would either be fetching hundreds of live quotes per page view
// (contradicting this page's own "refreshed daily, not fetched live per
// request" design) or inventing a number this pipeline doesn't have. "Net
// EMA20/EMA50 trend posture across the industry's monitored stocks" is the
// honest signal this data actually supports.
export interface SectorSummary {
  industry:        string;
  count:           number;
  marketCapCr:     number;
  bullishCount:    number;
  bearishCount:    number;
  avgRsi:          number | null;
  netTrendScore:   number;   // -1..1, (bullish - bearish) / (bullish + bearish); 0 when neither stock has a trend read yet
}

function summarizeBySector(stocks: ScreenerStock[]): SectorSummary[] {
  const map = new Map<string, { count: number; marketCapCr: number; bullish: number; bearish: number; rsiSum: number; rsiCount: number }>();
  for (const s of stocks) {
    const key = s.nse_industry ?? 'Unclassified';
    const entry = map.get(key) ?? { count: 0, marketCapCr: 0, bullish: 0, bearish: 0, rsiSum: 0, rsiCount: 0 };
    entry.count += 1;
    if (s.market_cap_cr != null) entry.marketCapCr += s.market_cap_cr;
    if (s.ema_trend === 'bullish') entry.bullish += 1;
    else if (s.ema_trend === 'bearish') entry.bearish += 1;
    if (s.rsi14 != null) { entry.rsiSum += s.rsi14; entry.rsiCount += 1; }
    map.set(key, entry);
  }
  return Array.from(map.entries())
    .map(([industry, e]) => {
      const trendTotal = e.bullish + e.bearish;
      return {
        industry,
        count: e.count,
        marketCapCr: e.marketCapCr,
        bullishCount: e.bullish,
        bearishCount: e.bearish,
        avgRsi: e.rsiCount > 0 ? e.rsiSum / e.rsiCount : null,
        netTrendScore: trendTotal > 0 ? (e.bullish - e.bearish) / trendTotal : 0,
      };
    })
    .sort((a, b) => b.marketCapCr - a.marketCapCr);
}

// Opacity magnitude scales with |score| (how lopsided the industry's
// bullish/bearish split is), not just its direction — an industry split
// evenly between bullish and bearish stays a flat neutral tile rather than
// being arbitrarily colored one way.
const _BUY_TONES  = ['text-buy border-buy/20 bg-buy/5', 'text-buy border-buy/25 bg-buy/12', 'text-buy border-buy/30 bg-buy/20', 'text-buy border-buy/40 bg-buy/30'];
const _SELL_TONES = ['text-sell border-sell/20 bg-sell/5', 'text-sell border-sell/25 bg-sell/12', 'text-sell border-sell/30 bg-sell/20', 'text-sell border-sell/40 bg-sell/30'];

function toneForScore(score: number): string {
  const mag = Math.min(3, Math.round(Math.abs(score) * 3));
  if (score > 0.15) return _BUY_TONES[mag];
  if (score < -0.15) return _SELL_TONES[mag];
  return 'text-hold border-hold/20 bg-hold/5';
}

export default function SectorHeatmap({
  stocks,
  activeIndustry,
  onSelectIndustry,
}: {
  stocks: ScreenerStock[];
  activeIndustry: string;
  onSelectIndustry: (industry: string) => void;
}) {
  const sectors = summarizeBySector(stocks);
  if (sectors.length === 0) return null;

  return (
    <div className="mb-6">
      <div className="flex items-center gap-2 mb-2.5">
        <p className="text-[10px] font-bold uppercase tracking-wider text-muted">Sector Momentum</p>
        <InfoTooltip title="Sector Momentum" align="left">
          <p>Each tile is one NIFTY 500 industry, colored by its net EMA20/EMA50 trend posture — the share of its monitored stocks currently in a bullish vs. bearish trend, from the same daily screener snapshot as the table below.</p>
          <p>Sized by position (biggest total market cap first), not a live day&apos;s % change — this pipeline refreshes once daily, not on every page view.</p>
          <p>Click a tile to filter the table to that industry; click again to clear it.</p>
        </InfoTooltip>
      </div>
      <div className="flex flex-wrap gap-2">
        {sectors.map(sec => {
          const active = activeIndustry === sec.industry;
          return (
            <button
              key={sec.industry}
              onClick={() => onSelectIndustry(active ? 'all' : sec.industry)}
              aria-pressed={active}
              title={`${sec.count} stock${sec.count === 1 ? '' : 's'} · ${sec.bullishCount} bullish / ${sec.bearishCount} bearish EMA trend${sec.avgRsi != null ? ` · avg RSI ${sec.avgRsi.toFixed(1)}` : ''} · ₹${sec.marketCapCr >= 1000 ? (sec.marketCapCr / 1000).toFixed(1) + 'k' : sec.marketCapCr.toFixed(0)} Cr combined`}
              className={`px-3 py-2 rounded-lg border text-left transition-colors ${toneForScore(sec.netTrendScore)} ${active ? 'ring-1 ring-accent/50' : ''}`}
            >
              <div className="text-[11px] font-semibold whitespace-nowrap">{sec.industry}</div>
              <div className="text-[10px] opacity-70">{sec.count} stock{sec.count === 1 ? '' : 's'}</div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
