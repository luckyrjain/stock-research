'use client';

import type { DcfEstimate, PeerComparison } from '@/types';
import InfoTooltip from './info-tooltip';
import { fmt } from './dashboard-format';

// A single "at a glance" reconciliation of every independent valuation lens
// this app computes for the same stock — the AI analyst's own Undervalued/
// Overvalued read, the deterministic DCF, where the P/E ranks against
// Screener-listed sector peers, where it ranks against its own trading
// history, and (when Trendlyne has one) the Street's mean target upside.
// Previously each of these only ever appeared in its own separate card, so
// seeing whether they agreed or disagreed meant scrolling and
// cross-referencing four places by hand. Renders only the lenses actually
// available for this stock — never a placeholder for a null/absent one —
// and renders nothing at all when fewer than two lenses have data, since a
// single value isn't something to "reconcile".
export default function ValuationSummaryStrip({
  llmVerdict,
  dcf,
  peerPePercentile,
  absoluteAnchor,
  streetUpsidePct,
}: {
  llmVerdict: string | undefined;
  dcf: DcfEstimate | null | undefined;
  peerPePercentile: number | undefined;
  absoluteAnchor: PeerComparison['absolute_anchor'];
  streetUpsidePct: number | null | undefined;
}) {
  const tone = (cheap: boolean | null): string =>
    cheap === true ? 'text-buy border-buy/25 bg-buy/10' : cheap === false ? 'text-sell border-sell/25 bg-sell/10' : 'text-hold border-hold/25 bg-hold/10';

  const items: { label: string; value: string; tone: string; title?: string }[] = [];

  if (llmVerdict) {
    items.push({
      label: 'AI Analyst',
      value: llmVerdict,
      tone: tone(llmVerdict === 'Undervalued' ? true : llmVerdict === 'Overvalued' ? false : null),
    });
  }
  if (dcf) {
    items.push({
      label: 'DCF',
      value: `${dcf.verdict} (${dcf.upside_pct >= 0 ? '+' : ''}${fmt(dcf.upside_pct, 1)}%)`,
      tone: tone(dcf.verdict === 'Undervalued' ? true : dcf.verdict === 'Overvalued' ? false : null),
      title: 'Deterministic two-stage discounted cash flow estimate — not LLM-generated',
    });
  }
  if (peerPePercentile != null) {
    items.push({
      label: 'vs. Peers',
      value: `${Math.round(peerPePercentile)}th pct P/E`,
      tone: tone(peerPePercentile <= 33 ? true : peerPePercentile >= 67 ? false : null),
      title: "Where this stock's P/E ranks among its Screener-listed sector peers — lower percentile is cheaper",
    });
  }
  if (absoluteAnchor) {
    items.push({
      label: 'vs. Own History',
      value: `${Math.round(absoluteAnchor.percentile)}th pct P/E`,
      tone: tone(absoluteAnchor.percentile <= 33 ? true : absoluteAnchor.percentile >= 67 ? false : null),
      title: `Current P/E ${fmt(absoluteAnchor.current_pe, 1)} vs. its own ${absoluteAnchor.years[0]}–${absoluteAnchor.years[absoluteAnchor.years.length - 1]} range (${fmt(absoluteAnchor.low, 1)}–${fmt(absoluteAnchor.high, 1)})`,
    });
  }
  if (streetUpsidePct != null) {
    items.push({
      label: 'Street Target',
      value: `${streetUpsidePct >= 0 ? '+' : ''}${fmt(streetUpsidePct, 1)}%`,
      tone: tone(streetUpsidePct >= 0),
      title: "Upside/downside to Trendlyne's scraped mean analyst target price",
    });
  }

  if (items.length < 2) return null;

  return (
    <div className="rounded-xl border border-border bg-card px-5 py-3.5 mb-5">
      <div className="flex items-center gap-2 mb-2.5">
        <p className="text-[10px] font-bold text-muted uppercase tracking-wider">Valuation, by lens</p>
        <InfoTooltip title="Valuation, by lens" align="left">
          <p>Every independent valuation read this app computes for this stock, side by side — the AI analyst&apos;s own read, a deterministic DCF, where its P/E ranks against sector peers, where its P/E ranks against its own trading history, and (when Trendlyne has one) the Street&apos;s mean target price.</p>
          <p>These can legitimately disagree — a stock can be cheap vs. peers and expensive vs. its own history at the same time. None of these corrects the others.</p>
        </InfoTooltip>
      </div>
      <div className="flex flex-wrap gap-2">
        {items.map(item => (
          <span
            key={item.label}
            title={item.title}
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[11px] font-semibold whitespace-nowrap ${item.tone}`}
          >
            <span className="opacity-70 font-normal">{item.label}</span>
            {item.value}
          </span>
        ))}
      </div>
    </div>
  );
}
