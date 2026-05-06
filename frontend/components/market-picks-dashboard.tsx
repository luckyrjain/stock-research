'use client';

import React, { useState } from 'react';
import type { MarketPick, PickSource } from '@/types';

interface Props {
  picks: MarketPick[];
  generatedAt: string;
  fromCache?: boolean;
  onRescan: () => void;
}

function fmt(n: number | null | undefined, decimals = 2): string {
  if (n == null) return '—';
  if (Math.abs(n) >= 1e5) return `₹${(n / 1e5).toFixed(1)}L Cr`;
  if (Math.abs(n) >= 1e3) return `₹${(n / 1e3).toFixed(1)}K Cr`;
  return n.toFixed(decimals);
}

function fmtPrice(n: number | null | undefined): string {
  if (n == null) return '—';
  return `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
}

function ConfidenceBar({ score }: { score: number }) {
  const color =
    score >= 70 ? 'bg-buy' :
    score >= 45 ? 'bg-hold' :
    'bg-sell';
  return (
    <div className="flex items-center gap-2 min-w-[120px]">
      <div className="flex-1 h-1.5 rounded-full bg-border overflow-hidden">
        <div
          className={`h-full rounded-full ${color} transition-all`}
          style={{ width: `${score}%` }}
        />
      </div>
      <span className="text-xs font-mono font-semibold text-tx w-10 text-right">
        {score.toFixed(0)}%
      </span>
    </div>
  );
}

function SignalBadge({ verdict }: { verdict: string }) {
  const cfg: Record<string, { bg: string; text: string }> = {
    BUY:       { bg: 'bg-buy/15 text-buy border-buy/30',       text: 'BUY' },
    WATCHLIST: { bg: 'bg-buy/10 text-buy/80 border-buy/20',   text: 'WATCH' },
    HOLD:      { bg: 'bg-hold/15 text-hold border-hold/30',    text: 'HOLD' },
    AVOID:     { bg: 'bg-sell/10 text-sell/80 border-sell/20', text: 'AVOID' },
    SELL:      { bg: 'bg-sell/15 text-sell border-sell/30',    text: 'SELL' },
  };
  const { bg, text } = cfg[verdict] ?? cfg.HOLD;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold border ${bg}`}>
      {text}
    </span>
  );
}

function IpoBadge({ isRecent }: { isRecent: boolean }) {
  if (!isRecent) return null;
  return (
    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold
      bg-accent/20 text-accent border border-accent/40"
      title="Recently listed (IPO < 8 months ago)"
    >
      IPO
    </span>
  );
}

function TrendBadge({ trend, delta }: { trend: MarketPick['trend']; delta: number | null }) {
  if (trend === 'new' || trend === 'stable') return null;
  const rising = trend === 'rising';
  return (
    <span className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold
      ${rising ? 'bg-buy/15 text-buy border border-buy/30' : 'bg-sell/15 text-sell border border-sell/30'}`}
      title={delta != null ? `${rising ? '+' : ''}${delta} vs recent avg` : undefined}
    >
      {rising ? '↑' : '↓'} {trend}
    </span>
  );
}

function SourcesPopover({ sources }: { sources: PickSource[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-accent/15 text-accent
                   text-[11px] font-semibold border border-accent/25 hover:bg-accent/25 transition-colors"
      >
        <span>{sources.length}</span>
        <span className="text-accent/70">firm{sources.length !== 1 ? 's' : ''}</span>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-full mt-2 z-20 w-64 bg-card border border-border
                          rounded-xl shadow-2xl shadow-black/50 overflow-hidden">
            <div className="px-3 py-2 border-b border-border">
              <span className="text-[11px] font-semibold text-muted uppercase tracking-wider">
                Reported by
              </span>
            </div>
            <ul className="divide-y divide-border max-h-48 overflow-y-auto">
              {sources.map((s, i) => (
                <li key={i} className="px-3 py-2 hover:bg-surface transition-colors">
                  <div className="flex items-center gap-2">
                    <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider
                      ${s.type === 'brokerage' ? 'bg-accent/20 text-accent' :
                        s.type === 'platform'  ? 'bg-hold/20 text-hold' :
                        'bg-muted/20 text-muted'}`}>
                      {s.type}
                    </span>
                    {s.direction && s.direction !== 'BUY' && (
                      <span className={`text-[9px] font-bold px-1 py-0.5 rounded border
                        ${s.direction === 'SELL' ? 'text-sell border-sell/40 bg-sell/10' : 'text-muted border-muted/30'}`}>
                        {s.direction}
                      </span>
                    )}
                    <span className="text-xs text-tx font-medium">{s.name}</span>
                  </div>
                  {s.headline && (
                    <p className="text-[10px] text-muted mt-0.5 leading-tight line-clamp-2">
                      {s.headline}
                    </p>
                  )}
                  {s.url && (
                    <a href={s.url} target="_blank" rel="noopener noreferrer"
                       className="text-[10px] text-accent hover:underline mt-0.5 block">
                      View article →
                    </a>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}

function TradeBox({ pick }: { pick: MarketPick }) {
  const p = (n: number | null) =>
    n != null ? `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 0 })}` : '—';

  const upside =
    pick.target_price != null && pick.current_price != null && pick.current_price > 0
      ? (((pick.target_price - pick.current_price) / pick.current_price) * 100).toFixed(1)
      : null;

  const riskReward =
    pick.target_price != null && pick.stop_loss != null && pick.entry_price != null &&
    pick.entry_price > pick.stop_loss
      ? ((pick.target_price - pick.entry_price) / (pick.entry_price - pick.stop_loss)).toFixed(1)
      : null;

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <div className="px-4 py-2 border-b border-border bg-surface flex items-center justify-between">
        <span className="text-[11px] font-bold text-muted uppercase tracking-wider">Trade Setup</span>
        {riskReward && (
          <span className="text-[10px] font-semibold text-accent">
            R:R = 1 : {riskReward}
          </span>
        )}
      </div>
      <div className="grid grid-cols-3 divide-x divide-border">
        {/* Entry */}
        <div className="px-4 py-3">
          <div className="text-[10px] text-muted mb-1 uppercase tracking-wider">Buy at</div>
          <div className="text-sm font-black text-tx">{p(pick.entry_price)}</div>
          <div className="text-[10px] text-muted mt-0.5">entry price</div>
        </div>
        {/* Target */}
        <div className="px-4 py-3">
          <div className="text-[10px] text-muted mb-1 uppercase tracking-wider">Target</div>
          <div className="text-sm font-black text-buy">{p(pick.target_price)}</div>
          {upside && (
            <div className="text-[10px] text-buy/70 mt-0.5">+{upside}% upside</div>
          )}
        </div>
        {/* Stop loss */}
        <div className="px-4 py-3">
          <div className="text-[10px] text-muted mb-1 uppercase tracking-wider">Stop Loss</div>
          <div className="text-sm font-black text-sell">{p(pick.stop_loss)}</div>
          {pick.stop_loss != null && pick.entry_price != null && pick.entry_price > 0 && (
            <div className="text-[10px] text-sell/70 mt-0.5">
              -{(((pick.entry_price - pick.stop_loss) / pick.entry_price) * 100).toFixed(1)}% risk
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ExpandedRow({ pick }: { pick: MarketPick }) {
  return (
    <tr className="animate-fade-up">
      <td colSpan={9} className="px-5 py-4 bg-surface border-b border-border">
        <div className="space-y-4">
          {/* Trade setup — always first */}
          <TradeBox pick={pick} />

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* Summary */}
            <div className="md:col-span-3">
              <p className="text-sm text-tx/90 leading-relaxed">{pick.summary || '—'}</p>
            </div>

            {/* Bull factors */}
            <div className="space-y-1.5">
              <h4 className="text-[11px] font-bold text-buy uppercase tracking-wider mb-2">
                Bull case
              </h4>
              {pick.bull_factors.length ? pick.bull_factors.map((f, i) => (
                <div key={i} className="flex gap-2 text-xs text-tx/80">
                  <span className="text-buy mt-0.5 shrink-0">▲</span>
                  <span>{f}</span>
                </div>
              )) : <span className="text-xs text-muted">—</span>}
            </div>

            {/* Bear factors */}
            <div className="space-y-1.5">
              <h4 className="text-[11px] font-bold text-sell uppercase tracking-wider mb-2">
                Bear case
              </h4>
              {pick.bear_factors.length ? pick.bear_factors.map((f, i) => (
                <div key={i} className="flex gap-2 text-xs text-tx/80">
                  <span className="text-sell mt-0.5 shrink-0">▼</span>
                  <span>{f}</span>
                </div>
              )) : <span className="text-xs text-muted">—</span>}
            </div>

            {/* Why ranked here + Key metrics */}
            <div className="space-y-3">
              {pick.ranking_reasons?.length > 0 && (
                <div>
                  <h4 className="text-[11px] font-bold text-accent uppercase tracking-wider mb-2">
                    Why ranked here
                  </h4>
                  <div className="space-y-1">
                    {pick.ranking_reasons.map((r, i) => (
                      <div key={i} className="flex gap-2 text-xs text-tx/80">
                        <span className="text-accent shrink-0">›</span>
                        <span>{r}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div>
                <h4 className="text-[11px] font-bold text-muted uppercase tracking-wider mb-2">
                  Key metrics
                </h4>
                <div className="grid grid-cols-2 gap-2">
                  {[
                    ['Conviction',   (pick.action_score * 100).toFixed(0) + '%'],
                    ['Quant signal', pick.signal_verdict],
                    ['Market cap',   pick.market_cap_cr != null ? `₹${pick.market_cap_cr.toLocaleString('en-IN', { maximumFractionDigits: 0 })} Cr` : '—'],
                    ['P/E ratio',    pick.pe_ratio != null ? pick.pe_ratio.toFixed(1) : '—'],
                    ['Exchange',     pick.exchange],
                    ['Signal score', pick.signal_score.toFixed(2)],
                  ].map(([label, value]) => (
                    <div key={label} className="bg-card rounded-lg px-2.5 py-1.5">
                      <div className="text-[10px] text-muted">{label}</div>
                      <div className="text-xs font-semibold text-tx">{value}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="mt-3 flex justify-end">
          <a
            href={`/?symbol=${pick.symbol}`}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
                       bg-accent/15 text-accent border border-accent/30 hover:bg-accent/25 transition-colors"
          >
            Full deep-dive analysis →
          </a>
        </div>
      </td>
    </tr>
  );
}

export default function MarketPicksDashboard({ picks, generatedAt, fromCache, onRescan }: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  function toggle(sym: string) {
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(sym) ? next.delete(sym) : next.add(sym);
      return next;
    });
  }

  const genDate = new Date(generatedAt).toLocaleString('en-IN', {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: true,
  });

  return (
    <div className="animate-fade-up">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-lg font-black text-tx">
            {picks.length} stocks found this week
          </h2>
          <div className="flex items-center gap-2 mt-0.5">
            <p className="text-xs text-muted">
              BUYs first · sorted by conviction · {fromCache ? 'Cached scan from' : 'Generated at'} {genDate}
            </p>
            {fromCache && (
              <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded
                               bg-hold/15 text-hold border border-hold/25">
                cached
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {fromCache && (
            <button
              onClick={onRescan}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-accent/30
                         text-accent hover:bg-accent/10 transition-colors"
            >
              ↺ Fresh scan
            </button>
          )}
          {!fromCache && (
            <button
              onClick={onRescan}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-border
                         text-muted hover:text-tx hover:border-border-hi transition-colors"
            >
              ↺ Rescan
            </button>
          )}
        </div>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 mb-4 text-[10px] text-muted uppercase tracking-wider">
        <div className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-buy inline-block" />High confidence (&gt;70)</div>
        <div className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-hold inline-block" />Medium (45–70)</div>
        <div className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-sell inline-block" />Low (&lt;45)</div>
      </div>

      {/* Table */}
      <div className="rounded-xl border border-border overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-surface">
                {['#', 'Stock', 'Confidence', 'Firms', 'Rec', 'Price', '±%', 'P/E', ''].map((h, i) => (
                  <th key={i}
                    className="px-4 py-3 text-left text-[10px] font-bold text-muted uppercase tracking-wider whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {picks.map(pick => {
                const isOpen = expanded.has(pick.symbol);
                const changePos = pick.change_pct >= 0;
                return (
                  <React.Fragment key={pick.symbol}>
                    <tr
                      onClick={() => toggle(pick.symbol)}
                      className={`border-b border-border cursor-pointer transition-colors
                        ${isOpen ? 'bg-card-hi' : 'hover:bg-surface'}`}
                    >
                      {/* Rank */}
                      <td className="px-4 py-3 w-8">
                        <span className={`text-xs font-black
                          ${pick.rank <= 3 ? 'text-accent' : 'text-muted'}`}>
                          {pick.rank}
                        </span>
                      </td>

                      {/* Stock */}
                      <td className="px-4 py-3">
                        <div className="flex flex-col gap-0.5">
                          <div className="flex items-center gap-1.5">
                            <span className="font-bold text-tx text-sm">{pick.symbol}</span>
                            <IpoBadge isRecent={pick.is_recent_ipo} />
                            <TrendBadge trend={pick.trend} delta={pick.trend_delta} />
                          </div>
                          <span className="text-[11px] text-muted truncate max-w-[160px]">
                            {pick.company}
                          </span>
                        </div>
                      </td>

                      {/* Confidence */}
                      <td className="px-4 py-3">
                        <ConfidenceBar score={pick.confidence_score} />
                      </td>

                      {/* Firms */}
                      <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
                        <SourcesPopover sources={pick.sources} />
                      </td>

                      {/* Recommendation */}
                      <td className="px-4 py-3">
                        <SignalBadge verdict={pick.recommendation} />
                      </td>

                      {/* Price */}
                      <td className="px-4 py-3 font-mono text-xs text-tx whitespace-nowrap">
                        {fmtPrice(pick.current_price)}
                      </td>

                      {/* Change % */}
                      <td className="px-4 py-3 font-mono text-xs whitespace-nowrap">
                        <span className={changePos ? 'text-buy' : 'text-sell'}>
                          {changePos ? '+' : ''}{pick.change_pct?.toFixed(2)}%
                        </span>
                      </td>

                      {/* P/E */}
                      <td className="px-4 py-3 font-mono text-xs text-muted">
                        {pick.pe_ratio != null ? pick.pe_ratio.toFixed(1) : '—'}
                      </td>

                      {/* Expand chevron */}
                      <td className="px-4 py-3 text-right">
                        <span className={`text-muted text-xs transition-transform inline-block
                          ${isOpen ? 'rotate-180' : ''}`}>
                          ▾
                        </span>
                      </td>
                    </tr>

                    {isOpen && <ExpandedRow pick={pick} />}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
